"""edit_file: the only way the agent changes file contents (F-37).

Operations: create, append, replace, insert. Guarantees:

  * An existing file is ALWAYS backed up through the journal first; if a backup
    cannot be made, the edit is refused (never edit without a way back).
  * Writes are atomic for normal files (temp file + rename) and keep the
    original permissions.
  * A unified diff is produced for the approval prompt (preview) and recorded
    in the journal.
  * Files in system folders (e.g. /etc) are written with administrator rights
    obtained directly from the user (privilege.ensure_admin); the password
    never touches the LLM.
  * The LLM never drives an interactive editor like nano or vim.
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from ..contracts import Reversibility, ToolResult
from .base import Preview, Tool, ToolContext, ToolError
from .files import guard_path, sha256_file
from .privilege import ensure_admin

MAX_EDIT_BYTES = 5 * 1024 * 1024
MAX_DIFF_CHARS = 100_000
PREVIEW_DIFF_CHARS = 4_000
OPERATIONS = ["create", "append", "replace", "insert"]


def _newline_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


_NO_NEWLINE = "\\ No newline at end of file\n"


def _diff_lines(text: str) -> list[str]:
    """Split into lines for diffing. A missing final newline is marked the way
    `git diff` does, so the last line is never glued to the next diff line."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\n"
        lines.append(_NO_NEWLINE)
    return lines


def make_diff(old: str, new: str, label: str) -> str:
    diff = "".join(difflib.unified_diff(_diff_lines(old), _diff_lines(new), fromfile=f"a/{label}", tofile=f"b/{label}"))
    return diff[:MAX_DIFF_CHARS]


def diff_stats(diff: str) -> tuple[int, int]:
    """(added, removed) line counts, ignoring headers and no-newline markers."""
    added = removed = 0
    for line in diff.splitlines():
        if line.startswith(("+++", "---")) or line[1:].startswith("\\ No newline"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


class EditFile(Tool):
    name = "edit_file"
    description = (
        "Change a text file safely. operation='create' makes a new file; 'append' adds text at the end; "
        "'replace' swaps one exact piece of text (find) for new text (content); 'insert' adds text before "
        "a line number. The original is backed up first so the edit can be undone. Use this instead of "
        "opening an editor or using shell redirects."
    )
    parameters = {
        "path": {"type": "string", "description": "File to change."},
        "operation": {"type": "string", "enum": OPERATIONS, "description": "What to do."},
        "content": {"type": "string", "description": "Text to write/append/insert, or the replacement text for 'replace'."},
        "find": {"type": "string", "description": "For 'replace': the exact existing text to replace."},
        "line": {"type": "integer", "description": "For 'insert': 1-based line number to insert before (use last line + 1 to add at the end)."},
        "replace_all": {"type": "boolean", "description": "For 'replace': replace every occurrence (default: the text must be unique)."},
        "overwrite": {"type": "boolean", "description": "For 'create': allow replacing an existing file (it is backed up first)."},
    }
    required = ("path", "operation")

    # ---- planning (shared by preview and run) ---------------------------------
    def validate(self, args: dict[str, Any]) -> Optional[str]:
        err = super().validate(args)
        if err:
            return err
        op = args["operation"]
        if op in {"create", "append", "insert", "replace"} and "content" not in args:
            return f"'content' is required for operation '{op}'"
        if op == "replace" and not args.get("find"):
            return "'find' (the exact text to replace) is required for operation 'replace'"
        if op == "insert" and not isinstance(args.get("line"), int):
            return "'line' (1-based line number) is required for operation 'insert'"
        return None

    def _plan(self, args: dict[str, Any], ctx: ToolContext) -> tuple[Path, Optional[str], str]:
        """Return (real_path, old_text_or_None_if_new, new_text). Raises ToolError."""
        path = ctx.resolve(args["path"])
        op = args["operation"]
        content: str = args.get("content", "")
        exists = path.exists()
        real = path.resolve() if exists else path
        guard_path(real, ctx, "edit")
        if exists and not real.is_file():
            raise ToolError(f"Not a regular file: {path}")

        if op == "create":
            if exists and not args.get("overwrite"):
                raise ToolError(f"{path} already exists. Use operation 'replace'/'append', or set overwrite=true (the old version is backed up).")
            if not path.parent.exists():
                raise ToolError(f"Folder does not exist: {path.parent}")
            old = self._read(real) if exists else None
            return real, old, content

        if not exists:
            raise ToolError(f"File not found: {path}. Use operation 'create' to make a new file.")
        old = self._read(real)
        nl = _newline_of(old)
        if op == "append":
            sep = "" if (not old or old.endswith(("\n", "\r"))) or content.startswith(("\n", "\r")) else nl
            return real, old, old + sep + content
        if op == "replace":
            find = args["find"]
            count = old.count(find)
            if count == 0:
                raise ToolError("The text to replace was not found, so nothing was changed. Read the file again and copy the exact text.")
            if count > 1 and not args.get("replace_all"):
                raise ToolError(f"The text to replace appears {count} times. Include more surrounding text to make it unique, or set replace_all=true.")
            return real, old, old.replace(find, content) if args.get("replace_all") else old.replace(find, content, 1)
        if op == "insert":
            lines = old.splitlines(keepends=True)
            line_no = args["line"]
            if line_no < 1 or line_no > len(lines) + 1:
                raise ToolError(f"Line {line_no} is out of range (the file has {len(lines)} line(s); valid: 1-{len(lines) + 1}).")
            ins = content if content.endswith(("\n", "\r")) else content + nl
            if line_no == len(lines) + 1 and lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += nl
            lines.insert(line_no - 1, ins)
            return real, old, "".join(lines)
        raise ToolError(f"Unknown operation: {op}")

    @staticmethod
    def _read(path: Path) -> str:
        try:
            if path.stat().st_size > MAX_EDIT_BYTES:
                raise ToolError(f"{path.name} is larger than {MAX_EDIT_BYTES // (1024 * 1024)} MB; refusing to edit it as text.")
            raw = path.read_bytes()
        except PermissionError as exc:
            raise ToolError(f"Cannot read {path} (permission denied), so it cannot be backed up or edited safely.") from exc
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(f"{path.name} is not a UTF-8 text file, so it cannot be edited safely.") from exc

    def _needs_admin(self, real: Path, ctx: ToolContext) -> bool:
        if ctx.env.is_root:
            return False
        target_dir = real.parent
        if real.exists():
            return not os.access(real, os.W_OK)
        return not os.access(target_dir, os.W_OK)

    # ---- preview / explain -----------------------------------------------------
    def explain(self, args: dict[str, Any], ctx: ToolContext) -> str:
        op = args.get("operation", "edit")
        verb = {"create": "Create", "append": "Add text to the end of", "replace": "Change some text in", "insert": "Insert text into"}.get(op, "Edit")
        return f"{verb} the file {args.get('path')} (a backup is kept)."

    def undo_hint_for(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return "Restore the saved backup of the original file."

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        pv = Preview(explanation=self.explain(args, ctx), reversibility=Reversibility.FULL, undo_hint=self.undo_hint_for(args, ctx))
        err = self.validate(args)
        if err:
            pv.warnings.append(err)
            return pv
        try:
            real, old, new = self._plan(args, ctx)
        except ToolError as exc:
            pv.warnings.append(str(exc))
            return pv
        diff = make_diff(old or "", new, real.name)
        pv.detail = diff[:PREVIEW_DIFF_CHARS] + ("\n[... diff shortened ...]" if len(diff) > PREVIEW_DIFF_CHARS else "")
        pv.needs_admin = self._needs_admin(real, ctx)
        if pv.needs_admin:
            pv.warnings.append("This file is in a protected location; administrator rights will be requested.")
        if old is None:
            pv.undo_hint = "Undo removes the new file."
        return pv

    # ---- execution ---------------------------------------------------------------
    def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        real, old, new = self._plan(args, ctx)
        existed = old is not None
        if old == new:
            return ToolResult(ok=True, output=f"No change needed: {real} already has this content.", data={"path": str(real), "changed": False})

        backup: dict[str, Any] = {}
        if existed:
            backup = ctx.journal.backup_file(real)
            if not backup.get("backup"):
                raise ToolError(f"Could not make a backup of {real}, so it was NOT edited.")

        privileged = self._needs_admin(real, ctx)
        data = new.encode("utf-8")
        if privileged:
            self._write_privileged(real, data, existed, ctx)
        else:
            self._write_atomic(real, data, existed)

        after = sha256_file(real)
        diff = make_diff(old or "", new, real.name)
        self._journal(
            ctx, f"edit_file {args['operation']} {real}",
            backups=[backup["backup"]] if backup.get("backup") else [],
            hashes={str(real): after} if after else {},
            diff=diff,
            undo={"op": "edit", "path": str(real), "created": not existed, "backup": backup.get("backup"),
                  "before_sha256": backup.get("sha256"), "after_sha256": after, "privileged": privileged},
        )
        added, removed = diff_stats(diff)
        verb = "Created" if not existed else "Edited"
        return ToolResult(
            ok=True, output=f"{verb} {real} (+{added} / -{removed} lines). Backup saved." if existed else f"Created {real}.",
            data={"path": str(real), "changed": True, "created": not existed, "diff": diff, "backup": backup.get("backup"), "privileged": privileged},
            reversibility=Reversibility.FULL, undo_hint="Restore the backup" if existed else "Remove the new file",
        )

    @staticmethod
    def _write_atomic(real: Path, data: bytes, existed: bool) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(real.parent), prefix=f".{real.name}.termiai-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            if existed:
                shutil.copymode(real, tmp)
                try:
                    st = real.stat()
                    os.chown(tmp, st.st_uid, st.st_gid)
                except (OSError, AttributeError):
                    pass
            else:
                umask = os.umask(0)
                os.umask(umask)
                os.chmod(tmp, 0o666 & ~umask)
            os.replace(tmp, real)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _write_privileged(real: Path, data: bytes, existed: bool, ctx: ToolContext) -> None:
        ensure_admin(ctx)
        tmpdir = tempfile.mkdtemp(prefix="termiai-edit-")
        try:
            os.chmod(tmpdir, 0o700)
            tmp = os.path.join(tmpdir, "content")
            with open(tmp, "wb") as fh:
                fh.write(data)
            mode, uid, gid = "644", "0", "0"
            if existed:
                st = real.stat()
                mode, uid, gid = format(st.st_mode & 0o7777, "o"), str(st.st_uid), str(st.st_gid)
            cmd = ["sudo", "-n", "install", "-m", mode, "-o", uid, "-g", gid, tmp, str(real)]
            proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                raise ToolError(f"Could not write {real} with administrator rights: {proc.stderr.strip() or 'unknown error'}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
