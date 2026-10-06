"""Inverse operations for the changes M3's tools record in the journal.

Every state-changing tool stores an `undo` dict in its JournalEntry. M4's
`/undo` and `rollback()` can call `undo_operation(entry.undo, ctx)` to reverse
exactly one entry (they decide the ORDER: newest first). Keeping the inverse
next to the tool that made the change means the two cannot drift apart.

Nothing here permanently deletes data: removing a copy or a created file sends
it to the Trash instead.
"""

from __future__ import annotations

import os
import shutil
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .base import ToolContext, ToolError
from .edit import EditFile
from .files import sha256_file


@dataclass
class UndoOutcome:
    ok: bool
    message: str
    conflict: bool = False  # True when the current state differs from what the tool left behind
    manual_steps: str = ""  # what the user can do by hand when ok is False


def _trash(path: Path) -> None:
    import send2trash

    send2trash.send2trash(str(path))


# ---- Trash lookup (so a trashed file can be restored) -----------------------
def _linux_trash_dirs() -> list[Path]:
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return [data_home / "Trash"]


def find_in_trash(original: Path) -> Optional[Path]:
    """Locate a file we trashed. Linux (freedesktop) and macOS home Trash only."""
    original = Path(original)
    if os.name == "nt":
        return None
    if Path("/System").exists():  # macOS
        candidate = Path.home() / ".Trash" / original.name
        return candidate if candidate.exists() else None
    for trash in _linux_trash_dirs():
        info_dir, files_dir = trash / "info", trash / "files"
        if not info_dir.is_dir():
            continue
        matches: list[tuple[float, Path]] = []
        for info in info_dir.glob("*.trashinfo"):
            try:
                text = info.read_text(errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                if line.startswith("Path="):
                    stored = urllib.parse.unquote(line[5:].strip())
                    if not os.path.isabs(stored):
                        stored = str(trash.parent.parent / stored)
                    if os.path.normpath(stored) == os.path.normpath(str(original)):
                        item = files_dir / info.stem
                        if item.exists() or item.is_symlink():
                            matches.append((info.stat().st_mtime, item))
        if matches:
            return max(matches)[1]  # most recently trashed
    return None


def _forget_trashinfo(item: Path) -> None:
    info = item.parent.parent / "info" / (item.name + ".trashinfo")
    try:
        info.unlink()
    except OSError:
        pass


# ---- individual inverses -------------------------------------------------------
def _undo_move(u: dict[str, Any]) -> UndoOutcome:
    cur, orig = Path(u["from"]), Path(u["to"])
    if not (cur.exists() or cur.is_symlink()):
        return UndoOutcome(False, f"{cur} is no longer there, so it cannot be moved back.", manual_steps=f"Find it and move it to {orig}.")
    if orig.exists() or orig.is_symlink():
        return UndoOutcome(False, f"Something already exists at {orig}.", conflict=True, manual_steps=f"Rename or move whatever is at {orig}, then move {cur} back.")
    shutil.move(str(cur), str(orig))
    return UndoOutcome(True, f"Moved {cur} back to {orig}.")


def _undo_copy(u: dict[str, Any]) -> UndoOutcome:
    created = Path(u["created"])
    if not (created.exists() or created.is_symlink()):
        return UndoOutcome(True, f"{created} was already removed.")
    _trash(created)
    return UndoOutcome(True, f"Moved the copy {created} to the Trash.")


def _undo_mkdir(u: dict[str, Any]) -> UndoOutcome:
    removed, kept = [], []
    for d in u.get("created", []):  # deepest first (recorded that way)
        p = Path(d)
        if not p.exists():
            continue
        try:
            p.rmdir()
            removed.append(d)
        except OSError:
            kept.append(d)
    if kept:
        return UndoOutcome(False, f"Folder(s) not empty, left in place: {', '.join(kept)}", conflict=True,
                           manual_steps="Empty them first, then remove the folders.")
    return UndoOutcome(True, f"Removed {len(removed)} empty folder(s).")


def _undo_trash(u: dict[str, Any]) -> UndoOutcome:
    original = Path(u["original"])
    if original.exists() or original.is_symlink():
        return UndoOutcome(False, f"Something already exists at {original}.", conflict=True,
                           manual_steps=f"Move it aside, then restore the item from the Trash to {original}.")
    item = find_in_trash(original)
    if item is not None:
        shutil.move(str(item), str(original))
        _forget_trashinfo(item)
        return UndoOutcome(True, f"Restored {original} from the Trash.")
    backup = u.get("backup")
    if backup and Path(backup).is_file():
        shutil.copy2(backup, original)
        return UndoOutcome(True, f"Restored {original} from the saved backup (it was not found in the Trash).")
    return UndoOutcome(False, f"Could not find {original} in the Trash and no backup exists.",
                       manual_steps="Open your Trash / Recycle Bin and restore it from there.")


def _undo_edit(u: dict[str, Any], ctx: ToolContext, force: bool) -> UndoOutcome:
    path = Path(u["path"])
    current = sha256_file(path) if path.exists() else None
    after = u.get("after_sha256")
    if not force and after and current is not None and current != after:
        return UndoOutcome(False, f"{path} was changed after Hasshr edited it, so it was not reverted.", conflict=True,
                           manual_steps=f"Compare {path} with the backup {u.get('backup')} and restore by hand, or undo with force.")
    if u.get("created"):
        if path.exists():
            _trash(path)
        return UndoOutcome(True, f"Removed the file {path} that Hasshr created (moved to the Trash).")
    backup = u.get("backup")
    if not backup or not Path(backup).is_file():
        return UndoOutcome(False, f"The backup for {path} is missing.", manual_steps="Restore the file from your own backup.")
    data = Path(backup).read_bytes()
    if u.get("privileged"):
        EditFile._write_privileged(path, data, path.exists(), ctx)
    else:
        EditFile._write_atomic(path, data, path.exists())
    restored = sha256_file(path)
    if u.get("before_sha256") and restored != u["before_sha256"]:
        return UndoOutcome(False, f"Restored {path}, but its checksum differs from the original.", conflict=True,
                           manual_steps="Check the file contents manually.")
    return UndoOutcome(True, f"Restored {path} from the backup.")


def _undo_bootonce(u: dict[str, Any], ctx: ToolContext) -> UndoOutcome:
    """Cancel a pending one-time boot request (clears GRUB's next_entry)."""
    import subprocess

    from .privilege import ensure_admin

    editenv = u.get("editenv")
    manual = "Run: sudo grub-editenv - unset next_entry"
    if not editenv:
        return UndoOutcome(False, "grub-editenv is not available to cancel the one-time boot.", manual_steps=manual)
    ensure_admin(ctx)
    proc = subprocess.run(["sudo", "-n", editenv, "-", "unset", "next_entry"], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return UndoOutcome(False, f"Could not cancel the one-time boot: {proc.stderr.strip()}", manual_steps=manual)
    return UndoOutcome(True, "Cancelled the one-time boot request; the next start uses the normal default.")


def _undo_shell(u: dict[str, Any]) -> UndoOutcome:
    rev = u.get("reversibility", "partial")
    return UndoOutcome(False, f"The shell command '{u.get('command')}' cannot be undone automatically (label: {rev}).",
                       manual_steps="Review the audit log for what it changed and reverse it by hand, or restore a system snapshot.")


def undo_operation(undo: dict[str, Any], ctx: ToolContext, *, force: bool = False) -> UndoOutcome:
    """Reverse one recorded change. Never raises for expected problems."""
    op = undo.get("op")
    try:
        if op == "move":
            return _undo_move(undo)
        if op == "copy":
            return _undo_copy(undo)
        if op == "mkdir":
            return _undo_mkdir(undo)
        if op == "trash":
            return _undo_trash(undo)
        if op == "edit":
            return _undo_edit(undo, ctx, force)
        if op == "bootonce":
            return _undo_bootonce(undo, ctx)
        if op == "shell":
            return _undo_shell(undo)
    except ToolError as exc:
        return UndoOutcome(False, str(exc))
    except OSError as exc:
        return UndoOutcome(False, f"Undo failed: {exc}", manual_steps="Check file permissions and try again.")
    return UndoOutcome(False, f"Don't know how to undo operation '{op}'.")
