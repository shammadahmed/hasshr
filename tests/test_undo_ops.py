import subprocess
from pathlib import Path

from hasshr.tools import undo_ops
from hasshr.tools.edit import EditFile
from hasshr.tools.files import CopyFile, CreateFolder, DeleteToTrash, MoveFile, sha256_file
from hasshr.tools.undo_ops import find_in_trash, undo_operation


def write(home: Path, rel: str, content: str) -> Path:
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


def last_undo(journal):
    return journal.entries[-1].undo


# ---------------------------------------------------------------- move
def test_undo_move(ctx, home, journal):
    src = write(home, "a.txt", "x")
    MoveFile().run({"source": "a.txt", "destination": "b.txt"}, ctx)
    out = undo_operation(last_undo(journal), ctx)
    assert out.ok and src.read_text() == "x" and not (home / "b.txt").exists()


def test_undo_move_conflict_when_original_slot_taken(ctx, home, journal):
    write(home, "a.txt", "x")
    MoveFile().run({"source": "a.txt", "destination": "b.txt"}, ctx)
    write(home, "a.txt", "someone else's file")
    out = undo_operation(last_undo(journal), ctx)
    assert not out.ok and out.conflict and out.manual_steps
    assert (home / "a.txt").read_text() == "someone else's file" and (home / "b.txt").exists()


def test_undo_move_when_moved_item_vanished(ctx, home, journal):
    write(home, "a.txt", "x")
    MoveFile().run({"source": "a.txt", "destination": "b.txt"}, ctx)
    (home / "b.txt").unlink()
    out = undo_operation(last_undo(journal), ctx)
    assert not out.ok and "no longer there" in out.message


# ---------------------------------------------------------------- copy
def test_undo_copy_sends_copy_to_trash_not_permanent_delete(ctx, home, journal, fake_trash):
    write(home, "a.txt", "x")
    CopyFile().run({"source": "a.txt", "destination": "b.txt"}, ctx)
    out = undo_operation(last_undo(journal), ctx)
    assert out.ok and (home / "a.txt").exists() and not (home / "b.txt").exists()
    assert any((fake_trash / "files").iterdir())  # recoverable


def test_undo_copy_already_gone_is_ok(ctx, home, journal, fake_trash):
    write(home, "a.txt", "x")
    CopyFile().run({"source": "a.txt", "destination": "b.txt"}, ctx)
    (home / "b.txt").unlink()
    assert undo_operation(last_undo(journal), ctx).ok


# ---------------------------------------------------------------- mkdir
def test_undo_mkdir_removes_only_empty_created_folders(ctx, home, journal):
    (home / "keep").mkdir()
    CreateFolder().run({"path": "keep/a/b"}, ctx)
    out = undo_operation(last_undo(journal), ctx)
    assert out.ok and (home / "keep").is_dir() and not (home / "keep/a").exists()


def test_undo_mkdir_leaves_non_empty_folder_and_reports(ctx, home, journal):
    CreateFolder().run({"path": "d"}, ctx)
    write(home, "d/user_file.txt", "added later")
    out = undo_operation(last_undo(journal), ctx)
    assert not out.ok and out.conflict and (home / "d/user_file.txt").exists() and "not empty" in out.message


# ---------------------------------------------------------------- trash
def test_find_in_trash_matches_by_original_path(home, fake_trash):
    import send2trash

    f = write(home, "docs/report one.txt", "r1")
    send2trash.send2trash(str(f))  # patched fake: url-quotes the path like the real spec
    found = find_in_trash(f)
    assert found is not None and found.read_text() == "r1"
    assert find_in_trash(home / "never-trashed.txt") is None


def test_find_in_trash_picks_most_recent_when_same_path_trashed_twice(home, fake_trash):
    import os

    import send2trash

    f = home / "dup.txt"
    f.write_text("first")
    send2trash.send2trash(str(f))
    f.write_text("second")
    send2trash.send2trash(str(f))
    first_info = fake_trash / "info" / "dup.txt.trashinfo"  # the fake names the first trashed copy 'dup.txt', the second 'dup.txt.2'
    assert (fake_trash / "files" / "dup.txt").read_text() == "first"
    os.utime(first_info, (1_000, 1_000))  # make the first one clearly older
    assert find_in_trash(f).read_text() == "second"
    os.utime(fake_trash / "info" / "dup.txt.2.trashinfo", (500, 500))  # now the second is older
    assert find_in_trash(f).read_text() == "first"


def test_undo_trash_restores_file_from_trash_and_cleans_trashinfo(ctx, home, journal, fake_trash):
    f = write(home, "old.txt", "keep me")
    DeleteToTrash().run({"path": "old.txt"}, ctx)
    assert not f.exists()
    out = undo_operation(last_undo(journal), ctx)
    assert out.ok and f.read_text() == "keep me"
    assert list((fake_trash / "info").glob("*.trashinfo")) == [] and list((fake_trash / "files").iterdir()) == []


def test_undo_trash_restores_folder_from_trash(ctx, home, journal, fake_trash):
    write(home, "proj/a.txt", "A")
    write(home, "proj/sub/b.txt", "B")
    DeleteToTrash().run({"path": "proj"}, ctx)
    assert undo_operation(last_undo(journal), ctx).ok
    assert (home / "proj/a.txt").read_text() == "A" and (home / "proj/sub/b.txt").read_text() == "B"


def test_undo_trash_falls_back_to_backup_when_trash_entry_is_gone(ctx, home, journal, fake_trash):
    f = write(home, "old.txt", "keep me")
    DeleteToTrash().run({"path": "old.txt"}, ctx)
    for p in (fake_trash / "files").iterdir():  # user emptied the trash
        p.unlink()
    for p in (fake_trash / "info").iterdir():
        p.unlink()
    out = undo_operation(last_undo(journal), ctx)
    assert out.ok and f.read_text() == "keep me" and "backup" in out.message


def test_undo_trash_folder_without_backup_or_trash_gives_manual_steps(ctx, home, journal, fake_trash):
    write(home, "proj/a.txt", "A")
    DeleteToTrash().run({"path": "proj"}, ctx)
    for p in list((fake_trash / "files").iterdir()):
        import shutil

        shutil.rmtree(p)
    out = undo_operation(last_undo(journal), ctx)
    assert not out.ok and "Trash" in out.manual_steps


def test_undo_trash_refuses_to_clobber_new_file_at_original_path(ctx, home, journal, fake_trash):
    write(home, "old.txt", "v1")
    DeleteToTrash().run({"path": "old.txt"}, ctx)
    write(home, "old.txt", "v2 created afterwards")
    out = undo_operation(last_undo(journal), ctx)
    assert not out.ok and out.conflict and (home / "old.txt").read_text() == "v2 created afterwards"


# ---------------------------------------------------------------- edit
def test_undo_edit_restores_exact_original(ctx, home, journal):
    f = write(home, "m.conf", "options a\n")
    before = sha256_file(f)
    EditFile().run({"path": "m.conf", "operation": "append", "content": "options b\n"}, ctx)
    out = undo_operation(last_undo(journal), ctx)
    assert out.ok and f.read_text() == "options a\n" and sha256_file(f) == before


def test_undo_edit_of_created_file_removes_it_via_trash(ctx, home, journal, fake_trash):
    EditFile().run({"path": "new.conf", "operation": "create", "content": "x"}, ctx)
    out = undo_operation(last_undo(journal), ctx)
    assert out.ok and not (home / "new.conf").exists() and any((fake_trash / "files").iterdir())


def test_undo_edit_detects_later_modification_and_force_overrides(ctx, home, journal):
    f = write(home, "m.conf", "orig\n")
    EditFile().run({"path": "m.conf", "operation": "append", "content": "ours\n"}, ctx)
    f.write_text("orig\nours\nuser added this later\n")
    out = undo_operation(last_undo(journal), ctx)
    assert not out.ok and out.conflict and "user added this later" in f.read_text()
    out = undo_operation(last_undo(journal), ctx, force=True)
    assert out.ok and f.read_text() == "orig\n"


def test_undo_edit_missing_backup(ctx, home, journal):
    write(home, "m.conf", "orig\n")
    EditFile().run({"path": "m.conf", "operation": "append", "content": "x\n"}, ctx)
    Path(journal.entries[-1].backups[0]).unlink()
    out = undo_operation(last_undo(journal), ctx)
    assert not out.ok and "backup" in out.message and out.manual_steps


def test_undo_privileged_edit_uses_admin_write(ctx, home, journal, monkeypatch):
    f = write(home, "m.conf", "orig\n")
    EditFile().run({"path": "m.conf", "operation": "append", "content": "x\n"}, ctx)
    undo = dict(last_undo(journal), privileged=True)
    seen = {}
    monkeypatch.setattr(EditFile, "_write_privileged", staticmethod(lambda real, data, existed, c: seen.update(path=real, data=data) or real.write_bytes(data)))
    out = undo_operation(undo, ctx)
    assert out.ok and seen["data"] == b"orig\n" and f.read_text() == "orig\n"


# ---------------------------------------------------------------- shell / boot-once / unknown
def test_shell_commands_are_reported_as_not_undoable(ctx):
    out = undo_operation({"op": "shell", "command": "apt install vim", "reversibility": "partial"}, ctx)
    assert not out.ok and "cannot be undone automatically" in out.message and out.manual_steps


def test_undo_boot_once_clears_next_entry(ctx, monkeypatch):
    monkeypatch.setattr("hasshr.tools.privilege.ensure_admin", lambda c: None)
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    out = undo_operation({"op": "bootonce", "editenv": "grub-editenv", "entry": "x"}, ctx)
    assert out.ok and seen["cmd"] == ["sudo", "-n", "grub-editenv", "-", "unset", "next_entry"]


def test_undo_boot_once_failure_and_missing_tool(ctx, monkeypatch):
    monkeypatch.setattr("hasshr.tools.privilege.ensure_admin", lambda c: None)
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "denied"))
    out = undo_operation({"op": "bootonce", "editenv": "grub-editenv"}, ctx)
    assert not out.ok and "unset next_entry" in out.manual_steps
    assert not undo_operation({"op": "bootonce", "editenv": None}, ctx).ok


def test_unknown_operation_and_os_errors_never_raise(ctx, monkeypatch):
    assert "Don't know" in undo_operation({"op": "teleport"}, ctx).message
    assert not undo_operation({}, ctx).ok

    def boom(*a, **k):
        raise PermissionError("denied")

    monkeypatch.setattr(undo_ops.shutil, "move", boom)
    out = undo_operation({"op": "move", "from": __file__, "to": "/tmp/definitely_free_target_xyz"}, ctx)
    assert not out.ok and "Undo failed" in out.message
