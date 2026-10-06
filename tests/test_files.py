import os
import time
from pathlib import Path

import pytest

from hasshr.contracts import Reversibility as R
from hasshr.tools.base import ToolError
from hasshr.tools.files import (
    CopyFile,
    CreateFolder,
    DeleteToTrash,
    FindFiles,
    ListFiles,
    MoveFile,
    ReadFile,
    guard_path,
    human_size,
    sha256_file,
)


def make(home: Path, rel: str, content: str = "x") -> Path:
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return p


# ---------------------------------------------------------------- helpers
def test_human_size():
    assert human_size(0) == "0 B" and human_size(1023) == "1023 B"
    assert human_size(1024) == "1.0 KB" and human_size(5 * 1024 * 1024) == "5.0 MB"


def test_sha256_file_and_limits(tmp_path):
    f = tmp_path / "a"
    f.write_text("abc")
    assert sha256_file(f) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256_file(tmp_path) is None  # directory
    assert sha256_file(tmp_path / "missing") is None


# ---------------------------------------------------------------- list
def test_list_files_sorts_folders_first_and_hides_dotfiles(ctx, home):
    make(home, "b.txt")
    make(home, "A.txt")
    make(home, ".hidden")
    (home / "zdir").mkdir()
    r = ListFiles().run({"path": str(home)}, ctx)
    names = [e["name"] for e in r.data["entries"]]
    assert names == ["zdir", "A.txt", "b.txt"]
    assert r.data["untrusted"] is True and r.reversibility == R.FULL
    assert ".hidden" in [e["name"] for e in ListFiles().run({"path": str(home), "show_hidden": True}, ctx).data["entries"]]


def test_list_files_limit_and_defaults(ctx, home):
    for i in range(5):
        make(home, f"f{i}.txt")
    r = ListFiles().run({"limit": 2}, ctx)  # defaults to cwd (= home)
    assert len(r.data["entries"]) == 2 and r.data["total"] == 5 and "showing first 2" in r.output


def test_list_files_errors(ctx, home):
    assert "not found" in ListFiles().run({"path": "nope"}, ctx).error.lower()
    f = make(home, "f.txt")
    assert "not a folder" in ListFiles().run({"path": str(f)}, ctx).error.lower()


# ---------------------------------------------------------------- find
def test_find_by_pattern_case_insensitive_and_hidden(ctx, home):
    make(home, "Downloads/a.PDF")
    make(home, "Downloads/sub/b.pdf")
    make(home, "Downloads/c.txt")
    make(home, ".cache/d.pdf")
    r = FindFiles().run({"root": "Downloads", "pattern": "*.pdf"}, ctx)
    assert r.data["total"] == 2
    r = FindFiles().run({"root": str(home), "pattern": "*.pdf"}, ctx)
    assert r.data["total"] == 2  # hidden folder skipped
    r = FindFiles().run({"root": str(home), "pattern": "*.pdf", "include_hidden": True}, ctx)
    assert r.data["total"] == 3


def test_find_largest_files_and_min_size(ctx, home):
    make(home, "small.bin", "x" * 10)
    make(home, "big.bin", "x" * 3 * 1024 * 1024)
    make(home, "mid.bin", "x" * 2000)
    r = FindFiles().run({"root": str(home), "sort_by": "size", "limit": 2}, ctx)
    assert [Path(f["path"]).name for f in r.data["files"]] == ["big.bin", "mid.bin"] and r.data["total"] == 3
    r = FindFiles().run({"root": str(home), "min_size_mb": 1}, ctx)
    assert [Path(f["path"]).name for f in r.data["files"]] == ["big.bin"]


def test_find_modified_within_days_and_depth(ctx, home):
    old = make(home, "old.txt")
    os.utime(old, (time.time() - 10 * 86400,) * 2)
    make(home, "new.txt")
    r = FindFiles().run({"root": str(home), "modified_within_days": 1}, ctx)
    assert [Path(f["path"]).name for f in r.data["files"]] == ["new.txt"]
    make(home, "l1/l2/l3/deep.txt")
    assert FindFiles().run({"root": str(home), "pattern": "deep.txt", "max_depth": 2}, ctx).data["total"] == 0
    assert FindFiles().run({"root": str(home), "pattern": "deep.txt", "max_depth": 4}, ctx).data["total"] == 1


def test_find_missing_root(ctx):
    assert not FindFiles().run({"root": "/definitely/not/here"}, ctx).ok


# ---------------------------------------------------------------- read
def test_read_file_text_truncation_and_offset(ctx, home):
    f = make(home, "t.txt", "0123456789" * 10)
    r = ReadFile().run({"path": str(f), "max_bytes": 10}, ctx)
    assert r.output.startswith("0123456789") and r.data["truncated"] and "truncated" in r.output
    r2 = ReadFile().run({"path": str(f), "max_bytes": 5, "offset": 95}, ctx)
    assert r2.output.startswith("56789") and not r2.data["truncated"]
    assert r.data["untrusted"] is True


def test_read_file_refuses_binary_and_missing_and_dir(ctx, home):
    b = home / "bin"
    b.write_bytes(b"\x00\x01\x02binary")
    assert "binary" in ReadFile().run({"path": str(b)}, ctx).error
    assert not ReadFile().run({"path": "nope"}, ctx).ok
    assert not ReadFile().run({"path": str(home)}, ctx).ok


def test_read_file_replaces_invalid_utf8_instead_of_crashing(ctx, home):
    f = home / "latin.txt"
    f.write_bytes("café".encode("latin-1"))
    r = ReadFile().run({"path": str(f)}, ctx)
    assert r.ok and "caf" in r.output


# ---------------------------------------------------------------- guard
def test_guard_refuses_root_home_and_system_folders(ctx, home):
    for bad in ("/", str(home), "/etc", "/usr", "/boot"):
        with pytest.raises(ToolError):
            guard_path(Path(bad), ctx, "delete")
    guard_path(home / "Downloads", ctx, "delete")  # ordinary path is fine
    guard_path(Path("/etc/hosts"), ctx, "edit")  # a file inside /etc is allowed (needs admin elsewhere)


# ---------------------------------------------------------------- move
def test_move_file_records_journal_and_hash(ctx, home, journal):
    src = make(home, "a.txt", "hello")
    dst = home / "b.txt"
    r = MoveFile().run({"source": "a.txt", "destination": "b.txt"}, ctx)
    assert r.ok and not src.exists() and dst.read_text() == "hello"
    e = journal.entries[-1]
    assert e.undo == {"op": "move", "from": str(dst), "to": str(src)}
    assert e.hashes[str(dst)] == sha256_file(dst) and e.command.startswith("move")


def test_move_into_existing_folder(ctx, home):
    make(home, "a.txt")
    (home / "PDFs").mkdir()
    r = MoveFile().run({"source": "a.txt", "destination": "PDFs"}, ctx)
    assert r.ok and (home / "PDFs" / "a.txt").exists()


def test_move_never_overwrites(ctx, home, journal):
    make(home, "a.txt", "A")
    make(home, "b.txt", "B")
    r = MoveFile().run({"source": "a.txt", "destination": "b.txt"}, ctx)
    assert not r.ok and "already exists" in r.error
    assert (home / "b.txt").read_text() == "B" and (home / "a.txt").exists()
    assert journal.entries == []  # nothing changed -> nothing journalled


def test_move_into_existing_folder_with_same_name_conflicts(ctx, home):
    make(home, "a.txt", "new")
    make(home, "dest/a.txt", "old")
    r = MoveFile().run({"source": "a.txt", "destination": "dest"}, ctx)
    assert not r.ok and (home / "dest" / "a.txt").read_text() == "old"


def test_move_errors(ctx, home):
    assert "not found" in MoveFile().run({"source": "nope", "destination": "x"}, ctx).error.lower()
    make(home, "a.txt")
    assert "does not exist" in MoveFile().run({"source": "a.txt", "destination": "no/such/dir/a.txt"}, ctx).error


def test_move_folder_into_itself_is_refused(ctx, home):
    (home / "d" / "sub").mkdir(parents=True)
    r = MoveFile().run({"source": "d", "destination": "d/sub"}, ctx)
    assert not r.ok and (home / "d").is_dir()


def test_move_refuses_home_and_root(ctx, home):
    assert not MoveFile().run({"source": str(home), "destination": str(home.parent / "elsewhere")}, ctx).ok
    assert home.exists()


def test_move_works_with_symlink_source(ctx, home):
    target = make(home, "real.txt")
    link = home / "link.txt"
    link.symlink_to(target)
    r = MoveFile().run({"source": "link.txt", "destination": "moved.txt"}, ctx)
    assert r.ok and (home / "moved.txt").is_symlink() and target.exists()


# ---------------------------------------------------------------- copy
def test_copy_file_and_folder(ctx, home, journal):
    make(home, "a.txt", "data")
    r = CopyFile().run({"source": "a.txt", "destination": "b.txt"}, ctx)
    assert r.ok and (home / "a.txt").exists() and (home / "b.txt").read_text() == "data"
    assert journal.entries[-1].undo["op"] == "copy"
    make(home, "d/x.txt", "1")
    r = CopyFile().run({"source": "d", "destination": "d2"}, ctx)
    assert r.ok and (home / "d2" / "x.txt").read_text() == "1" and journal.entries[-1].undo["is_dir"]


def test_copy_never_overwrites_and_rejects_self_copy(ctx, home):
    make(home, "a.txt", "A")
    make(home, "b.txt", "B")
    assert not CopyFile().run({"source": "a.txt", "destination": "b.txt"}, ctx).ok
    assert (home / "b.txt").read_text() == "B"
    (home / "d").mkdir()
    assert not CopyFile().run({"source": "d", "destination": "d/inner"}, ctx).ok


# ---------------------------------------------------------------- mkdir
def test_create_folder_records_only_new_parents(ctx, home, journal):
    (home / "exists").mkdir()
    r = CreateFolder().run({"path": "exists/a/b"}, ctx)
    assert r.ok and (home / "exists/a/b").is_dir()
    created = journal.entries[-1].undo["created"]
    assert created == [str(home / "exists/a/b"), str(home / "exists/a")]  # deepest first; 'exists' not included


def test_create_folder_is_idempotent_and_detects_file_clash(ctx, home, journal):
    CreateFolder().run({"path": "d"}, ctx)
    n = len(journal.entries)
    r = CreateFolder().run({"path": "d"}, ctx)
    assert r.ok and r.data["created"] is False and len(journal.entries) == n
    make(home, "f")
    assert not CreateFolder().run({"path": "f"}, ctx).ok


# ---------------------------------------------------------------- trash
def test_delete_file_to_trash_backs_up_and_is_full(ctx, home, journal, fake_trash):
    f = make(home, "old.txt", "keep me")
    pv = DeleteToTrash().preview({"path": "old.txt"}, ctx)
    assert pv.reversibility == R.FULL
    r = DeleteToTrash().run({"path": "old.txt"}, ctx)
    assert r.ok and not f.exists() and r.reversibility == R.FULL
    e = journal.entries[-1]
    assert e.undo["op"] == "trash" and e.undo["has_backup"] and Path(e.backups[0]).read_text() == "keep me"
    assert any((fake_trash / "files").iterdir())


def test_delete_folder_to_trash_is_partial(ctx, home, journal, fake_trash):
    make(home, "proj/a.txt")
    make(home, "proj/b.txt")
    pv = DeleteToTrash().preview({"path": "proj"}, ctx)
    assert pv.reversibility == R.PARTIAL and any("item" in w for w in pv.warnings)
    r = DeleteToTrash().run({"path": "proj"}, ctx)
    assert r.ok and not (home / "proj").exists() and r.reversibility == R.PARTIAL
    assert journal.entries[-1].undo["has_backup"] is False


def test_delete_refuses_dangerous_paths_and_missing(ctx, home, fake_trash):
    for bad in (str(home), "/", "/etc"):
        r = DeleteToTrash().run({"path": bad}, ctx)
        assert not r.ok and "Refusing" in r.error
    assert home.exists()
    assert "Nothing found" in DeleteToTrash().run({"path": "ghost"}, ctx).error
