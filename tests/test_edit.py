import os
import stat
import subprocess
from pathlib import Path

from termiai.contracts import Reversibility as R
from termiai.tools import edit as edit_mod
from termiai.tools.edit import EditFile, make_diff
from termiai.tools.files import sha256_file


def write(home: Path, rel: str, content: str, mode: int | None = None) -> Path:
    p = home / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content.encode())
    if mode is not None:
        p.chmod(mode)
    return p


def edit(ctx, **kw):
    return EditFile().run(kw, ctx)


# ---------------------------------------------------------------- create
def test_create_new_file(ctx, home, journal):
    r = edit(ctx, path="conf.txt", operation="create", content="a=1\n")
    assert r.ok and (home / "conf.txt").read_text() == "a=1\n" and r.data["created"]
    e = journal.entries[-1]
    assert e.undo["created"] is True and e.undo["op"] == "edit" and e.hashes[str(home / "conf.txt")]
    assert not e.backups


def test_create_refuses_existing_unless_overwrite(ctx, home, journal):
    f = write(home, "c.txt", "old")
    r = edit(ctx, path="c.txt", operation="create", content="new")
    assert not r.ok and "already exists" in r.error and f.read_text() == "old"
    r = edit(ctx, path="c.txt", operation="create", content="new", overwrite=True)
    assert r.ok and f.read_text() == "new"
    assert Path(journal.entries[-1].backups[0]).read_text() == "old"  # old version preserved


def test_create_needs_existing_parent(ctx):
    assert "does not exist" in edit(ctx, path="no/dir/f.txt", operation="create", content="x").error


# ---------------------------------------------------------------- append
def test_append_adds_missing_newline_separator(ctx, home):
    f = write(home, "a.conf", "line1")
    assert edit(ctx, path="a.conf", operation="append", content="line2\n").ok
    assert f.read_text() == "line1\nline2\n"


def test_append_to_file_ending_in_newline_adds_no_blank_line(ctx, home):
    f = write(home, "a.conf", "line1\n")
    edit(ctx, path="a.conf", operation="append", content="line2\n")
    assert f.read_text() == "line1\nline2\n"


def test_append_preserves_crlf_style(ctx, home):
    f = write(home, "w.txt", "a\r\nb")
    edit(ctx, path="w.txt", operation="append", content="c")
    assert f.read_bytes() == b"a\r\nb\r\nc"


def test_append_to_missing_file_points_to_create(ctx):
    r = edit(ctx, path="nope.txt", operation="append", content="x")
    assert not r.ok and "create" in r.error


# ---------------------------------------------------------------- replace
def test_replace_unique_text(ctx, home):
    f = write(home, "m.conf", "options snd-hda-intel model=auto\nother\n")
    r = edit(ctx, path="m.conf", operation="replace", find="model=auto", content="model=laptop")
    assert r.ok and f.read_text() == "options snd-hda-intel model=laptop\nother\n"
    assert "-options" in r.data["diff"] and "+options" in r.data["diff"]


def test_replace_requires_unique_unless_replace_all(ctx, home):
    f = write(home, "m.conf", "x\nx\n")
    r = edit(ctx, path="m.conf", operation="replace", find="x", content="y")
    assert not r.ok and "2 times" in r.error and f.read_text() == "x\nx\n"
    assert edit(ctx, path="m.conf", operation="replace", find="x", content="y", replace_all=True).ok
    assert f.read_text() == "y\ny\n"


def test_replace_text_not_found_changes_nothing(ctx, home, journal):
    f = write(home, "m.conf", "abc")
    r = edit(ctx, path="m.conf", operation="replace", find="zzz", content="y")
    assert not r.ok and "not found" in r.error and f.read_text() == "abc"
    assert journal.entries == [] and not list(journal.backup_dir.iterdir())  # no backup, no entry on failure


def test_replace_with_empty_content_deletes_text(ctx, home):
    f = write(home, "m.conf", "keep\nremove me\n")
    assert edit(ctx, path="m.conf", operation="replace", find="remove me\n", content="").ok
    assert f.read_text() == "keep\n"


# ---------------------------------------------------------------- insert
def test_insert_before_line_and_at_end(ctx, home):
    f = write(home, "i.txt", "a\nb\nc\n")
    assert edit(ctx, path="i.txt", operation="insert", line=2, content="X").ok
    assert f.read_text() == "a\nX\nb\nc\n"
    assert edit(ctx, path="i.txt", operation="insert", line=5, content="END").ok
    assert f.read_text() == "a\nX\nb\nc\nEND\n"


def test_insert_at_end_of_file_without_trailing_newline(ctx, home):
    f = write(home, "i.txt", "a\nb")
    edit(ctx, path="i.txt", operation="insert", line=3, content="c")
    assert f.read_text() == "a\nb\nc\n"


def test_insert_out_of_range(ctx, home):
    write(home, "i.txt", "a\n")
    for bad in (0, 3, -1):
        assert "out of range" in edit(ctx, path="i.txt", operation="insert", line=bad, content="x").error


# ---------------------------------------------------------------- validation
def test_argument_validation(ctx, home):
    write(home, "f.txt", "x")
    assert "content" in edit(ctx, path="f.txt", operation="append").error
    assert "find" in edit(ctx, path="f.txt", operation="replace", content="y").error
    assert "line" in edit(ctx, path="f.txt", operation="insert", content="y").error
    assert "one of" in edit(ctx, path="f.txt", operation="delete", content="y").error


# ---------------------------------------------------------------- safety
def test_backup_and_hashes_recorded(ctx, home, journal):
    f = write(home, "f.txt", "before")
    before = sha256_file(f)
    edit(ctx, path="f.txt", operation="append", content="after")
    e = journal.entries[-1]
    assert Path(e.backups[0]).read_text() == "before"
    assert e.undo["before_sha256"] == before and e.undo["after_sha256"] == sha256_file(f) != before
    assert e.diff and "+after" in e.diff


def test_refuses_to_edit_when_backup_cannot_be_made(ctx, home):
    f = write(home, "f.txt", "precious")

    class BrokenJournal:
        def backup_file(self, path):
            return {}

        def record(self, entry):
            raise AssertionError("must not record a failed edit")

    ctx.journal = BrokenJournal()
    r = edit(ctx, path="f.txt", operation="append", content="x")
    assert not r.ok and "NOT edited" in r.error and f.read_text() == "precious"


def test_no_change_is_a_noop(ctx, home, journal):
    write(home, "f.txt", "same")
    r = edit(ctx, path="f.txt", operation="replace", find="same", content="same")
    assert r.ok and r.data["changed"] is False and journal.entries == []


def test_refuses_binary_and_huge_and_directories(ctx, home, monkeypatch):
    b = home / "b.bin"
    b.write_bytes(b"\xff\xfe\x00\x01")
    assert "UTF-8" in edit(ctx, path="b.bin", operation="append", content="x").error
    big = write(home, "big.txt", "x" * 100)
    monkeypatch.setattr(edit_mod, "MAX_EDIT_BYTES", 10)
    assert "larger than" in edit(ctx, path="big.txt", operation="append", content="x").error
    assert not edit(ctx, path=str(home), operation="append", content="x").ok
    assert big.read_text() == "x" * 100


def test_refuses_system_roots_and_home(ctx, home):
    assert "Refusing" in edit(ctx, path="/", operation="create", content="x").error


def test_permissions_preserved(ctx, home):
    f = write(home, "script.sh", "#!/bin/sh\n", mode=0o750)
    edit(ctx, path="script.sh", operation="append", content="echo hi\n")
    assert stat.S_IMODE(f.stat().st_mode) == 0o750


def test_new_file_respects_umask(ctx, home):
    old = os.umask(0o077)
    try:
        edit(ctx, path="new.txt", operation="create", content="x")
    finally:
        os.umask(old)
    assert stat.S_IMODE((home / "new.txt").stat().st_mode) == 0o600


def test_edit_through_symlink_edits_target_and_keeps_link(ctx, home):
    real = write(home, "real.conf", "a")
    link = home / "link.conf"
    link.symlink_to(real)
    edit(ctx, path="link.conf", operation="append", content="b")
    assert link.is_symlink() and real.read_text() == "a\nb"


def test_no_temp_files_left_behind(ctx, home):
    write(home, "f.txt", "a")
    edit(ctx, path="f.txt", operation="append", content="b")
    assert [p.name for p in home.iterdir()] == ["f.txt"]


def test_atomic_write_failure_leaves_original_intact(ctx, home, monkeypatch):
    f = write(home, "f.txt", "orig")

    def boom(src, dst):
        raise OSError("disk exploded")

    monkeypatch.setattr(os, "replace", boom)
    r = edit(ctx, path="f.txt", operation="append", content="x")
    assert not r.ok and f.read_text() == "orig"
    assert [p.name for p in home.iterdir()] == ["f.txt"]  # temp file cleaned up


# ---------------------------------------------------------------- preview
def test_preview_shows_diff_without_writing(ctx, home):
    f = write(home, "f.txt", "one\n")
    pv = EditFile().preview({"path": "f.txt", "operation": "append", "content": "two\n"}, ctx)
    assert "+two" in pv.detail and pv.reversibility == R.FULL and f.read_text() == "one\n"


def test_preview_reports_problems_as_warnings(ctx, home):
    pv = EditFile().preview({"path": "ghost.txt", "operation": "append", "content": "x"}, ctx)
    assert pv.warnings and "not found" in pv.warnings[0].lower()
    pv = EditFile().preview({"path": "ghost.txt", "operation": "replace"}, ctx)
    assert pv.warnings


def test_preview_diff_is_shortened(ctx, home):
    write(home, "f.txt", "x\n")
    pv = EditFile().preview({"path": "f.txt", "operation": "append", "content": "line\n" * 5000}, ctx)
    assert "shortened" in pv.detail and len(pv.detail) < 5000


def test_make_diff_labels():
    d = make_diff("a\n", "b\n", "f.conf")
    assert "--- a/f.conf" in d and "+++ b/f.conf" in d


# ---------------------------------------------------------------- privileged writes
def test_protected_file_uses_sudo_install_with_original_owner(ctx, home, journal, monkeypatch):
    f = write(home, "etc_hosts", "127.0.0.1 localhost\n", mode=0o644)
    monkeypatch.setattr(EditFile, "_needs_admin", lambda self, real, c: True)
    calls = {}
    monkeypatch.setattr(edit_mod, "ensure_admin", lambda c: calls.setdefault("admin", True))

    def fake_run(cmd, **kw):
        calls["cmd"], calls["kw"] = cmd, kw
        calls["staged"] = Path(cmd[-2]).read_text()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = edit(ctx, path="etc_hosts", operation="append", content="10.0.0.1 db\n")
    assert r.ok and r.data["privileged"] is True and calls["admin"]
    cmd = calls["cmd"]
    assert cmd[:4] == ["sudo", "-n", "install", "-m"] and cmd[-1] == str(f)
    assert cmd[cmd.index("-m") + 1] == "644"
    assert cmd[cmd.index("-o") + 1] == str(f.stat().st_uid)
    assert calls["staged"].endswith("10.0.0.1 db\n")  # content staged in a temp file, never on the command line
    assert "10.0.0.1" not in " ".join(cmd)
    assert calls["kw"]["stdin"] == subprocess.DEVNULL  # no password path through us
    assert journal.entries[-1].undo["privileged"] is True


def test_protected_write_failure_is_reported_and_temp_removed(ctx, home, monkeypatch):
    write(home, "p.conf", "a")
    monkeypatch.setattr(EditFile, "_needs_admin", lambda self, real, c: True)
    monkeypatch.setattr(edit_mod, "ensure_admin", lambda c: None)
    staged = {}

    def fake_run(cmd, **kw):
        staged["dir"] = Path(cmd[-2]).parent
        return subprocess.CompletedProcess(cmd, 1, "", "install: cannot create regular file: Permission denied")

    monkeypatch.setattr(subprocess, "run", fake_run)
    r = edit(ctx, path="p.conf", operation="append", content="b")
    assert not r.ok and "administrator" in r.error and not staged["dir"].exists()


def test_needs_admin_detection(ctx, home, monkeypatch):
    f = write(home, "ok.txt", "x")
    assert EditFile()._needs_admin(f, ctx) is False
    monkeypatch.setattr(os, "access", lambda p, m: False)
    assert EditFile()._needs_admin(f, ctx) is True
    ctx.env.is_root = True
    assert EditFile()._needs_admin(f, ctx) is False


# ---------------------------------------------------------------- diff correctness
def test_diff_marks_missing_final_newline_instead_of_gluing_lines(ctx, home):
    from termiai.tools.edit import diff_stats

    write(home, "c.txt", "c.txt")  # no trailing newline
    r = edit(ctx, path="c.txt", operation="replace", find="c.txt", content="d.txt")
    lines = r.data["diff"].splitlines()
    assert "-c.txt" in lines and "+d.txt" in lines  # each on its own line, not glued together
    assert any("No newline at end of file" in ln for ln in lines)
    assert diff_stats(r.data["diff"]) == (1, 1) and "+1 / -1" in r.output


def test_append_to_unterminated_file_diff_shows_only_new_line(ctx, home):
    from termiai.tools.edit import diff_stats

    write(home, "c.txt", "one")
    r = edit(ctx, path="c.txt", operation="append", content="two")
    assert (home / "c.txt").read_text() == "one\ntwo"
    assert "+two" in r.data["diff"].splitlines() and diff_stats(r.data["diff"]) == (1, 0)


def test_diff_stats_ignores_headers_and_markers():
    from termiai.tools.edit import diff_stats

    d = make_diff("a\nb", "a\nc\n", "f")
    assert diff_stats(d) == (1, 1)
    assert diff_stats("") == (0, 0)
