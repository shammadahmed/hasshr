import tempfile
from pathlib import Path

import pytest

from hasshr.contracts import Context, Reversibility, RiskLevel, RiskResult, ToolCall, ToolResult
from hasshr.journal import (
    ActionJournal as Journal,
)
from hasshr.journal import (
    CaseJournal,
    SnapshotManager,
    UndoManager,
    Verifier,
)
from hasshr.journal import (
    Journal as StoreJournal,
)


def test_snapshot_delete_and_undo():
    with tempfile.TemporaryDirectory() as d:
        base, target = Path(d) / ".hasshr", Path(d) / "demo.txt"
        target.write_text("original", encoding="utf-8")
        j, s, v = Journal(base), SnapshotManager(base), Verifier()
        a = j.start_action("Delete demo.txt", "DELETE", str(target), reversibility="Full")
        m = s.snapshot_file(target, action_id=a.action_id)
        a = j.update(a, snapshot_id=m["snapshot_id"])
        target.unlink()
        ok, details = v.verify_deleted(target)
        a = j.update(
            a,
            status="SUCCESS",
            verification_status="PASSED" if ok else "FAILED",
            verification_details=details,
            undo_available=ok,
        )
        UndoManager(base).undo(a.action_id)
        assert target.read_text(encoding="utf-8") == "original"


def test_restore_conflict_requires_explicit_overwrite():
    with tempfile.TemporaryDirectory() as d:
        base, target = Path(d) / ".hasshr", Path(d) / "x.txt"
        target.write_text("old", encoding="utf-8")
        m = SnapshotManager(base).snapshot_file(target)
        target.write_text("new", encoding="utf-8")
        with pytest.raises(FileExistsError):
            SnapshotManager(base).restore(m["snapshot_id"])
        SnapshotManager(base).restore(m["snapshot_id"], overwrite=True)
        assert target.read_text(encoding="utf-8") == "old"


def test_secret_redaction_and_corrupt_tail_tolerance():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d) / ".hasshr"
        j = Journal(base)
        a = j.start_action("use api_key=SUPERSECRET", "RUN", "x")
        j.update(a, output="password=hunter2")
        with j.path.open("a", encoding="utf-8") as f:
            f.write("{broken\n")
        text = j.path.read_text(encoding="utf-8")
        assert "SUPERSECRET" not in text and "hunter2" not in text
        assert j.find(a.action_id) is not None


def test_case_attempt_persistence():
    with tempfile.TemporaryDirectory() as d:
        data = CaseJournal(Path(d) / ".hasshr").record_attempt(
            "CASE-0001",
            "Service down",
            1,
            "Bad config",
            "Read config",
            "Mismatch found",
            evidence="line 12",
            confidence="HIGH",
            next_test="Validate corrected config",
        )
        assert data["attempts"][0]["hypothesis"] == "Bad config"


def test_store_journal_integration(tmp_path: Path):
    j = StoreJournal(path=tmp_path / "journal.jsonl")
    ctx = Context()
    call = ToolCall(name="test_tool", args={"path": str(tmp_path / "f.txt")})
    
    # Create test file to back up
    (tmp_path / "f.txt").write_text("before content")
    entry = j.before(call, "test command", RiskResult(RiskLevel.SAFE), Reversibility.FULL, ctx)
    assert entry.tool == "test_tool"
    assert len(j.entries) == 1
    
    res = ToolResult(ok=True, output="secret_key=xyz")
    j.after(entry, res)
    assert entry.ok is True
    assert "secret_key" not in entry.output
    
    j.audit("test_event", {"token": "MY_SECRET_TOKEN"})
    assert len(j.audit_log) == 1
    assert "MY_SECRET_TOKEN" not in str(j.audit_log[0])
    
    # History check
    h = j.history()
    assert len(h) >= 1
