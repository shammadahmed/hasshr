from __future__ import annotations

from conftest import FakeApprover, make_pipeline

from hasshr.contracts import (
    ApprovalChoice,
    ApprovalResponse,
    Mode,
    RiskLevel,
    ToolCall,
)


def shell(cmd: str, **kw) -> ToolCall:
    return ToolCall("run_shell", {"command": cmd}, **kw)


def spy(registry):
    return registry.get("run_shell")


def test_unknown_tool(registry, journal, ctx):
    result = make_pipeline(registry, journal).execute(ToolCall("nope"), ctx)
    assert not result.ok and "Unknown tool" in result.error


def test_safe_command_runs_without_asking(registry, journal, ctx):
    approver = FakeApprover()
    result = make_pipeline(registry, journal, approver).execute(shell("ls /tmp"), ctx)
    assert result.ok and approver.requests == []
    assert spy(registry).ran == ["ls /tmp"]


def test_sensitive_asks_in_ask_sensitive_and_runs_when_approved(registry, journal, ctx):
    approver = FakeApprover(ApprovalChoice.YES)
    result = make_pipeline(registry, journal, approver).execute(shell("sudo apt update"), ctx)
    assert result.ok and len(approver.requests) == 1
    assert approver.requests[0].risk.level == RiskLevel.SENSITIVE


def test_declined_never_runs(registry, journal, ctx):
    result = make_pipeline(registry, journal, FakeApprover(ApprovalChoice.NO)).execute(
        shell("sudo apt update"), ctx
    )
    assert result.declined and not result.ok
    assert spy(registry).ran == []


def test_default_approver_denies(registry, journal, ctx):
    result = make_pipeline(registry, journal).execute(shell("sudo apt update"), ctx)
    assert result.declined and spy(registry).ran == []


def test_blocked_is_refused_in_every_mode(registry, journal, ctx):
    for mode in Mode:
        ctx.mode = mode
        approver = FakeApprover(ApprovalChoice.YES)
        result = make_pipeline(registry, journal, approver).execute(shell("rm -rf /"), ctx)
        assert result.refused and approver.requests == []
    assert spy(registry).ran == []


def test_auto_mode_runs_sensitive_without_asking(registry, journal, ctx):
    ctx.mode = Mode.AUTO
    approver = FakeApprover()
    result = make_pipeline(registry, journal, approver).execute(shell("sudo apt update"), ctx)
    assert result.ok and approver.requests == []


def test_irreversible_still_asks_in_auto(registry, journal, ctx):
    ctx.mode = Mode.AUTO
    approver = FakeApprover(ApprovalChoice.NO)
    result = make_pipeline(registry, journal, approver).execute(shell("rm -r /tmp/x"), ctx)
    assert result.declined and len(approver.requests) == 1


def test_ask_all_asks_for_changes_but_not_reads(registry, journal, ctx, tmp_path):
    ctx.mode = Mode.ASK_ALL
    approver = FakeApprover(ApprovalChoice.YES)
    pipe = make_pipeline(registry, journal, approver)
    pipe.execute(ToolCall("list_files", {"path": str(tmp_path)}), ctx)
    assert approver.requests == []
    pipe.execute(ToolCall("create_folder", {"path": str(tmp_path / "new")}), ctx)
    assert len(approver.requests) == 1 and (tmp_path / "new").is_dir()


def test_llm_can_raise_risk_but_never_lower_it(registry, journal, ctx):
    approver = FakeApprover(ApprovalChoice.NO)
    pipe = make_pipeline(registry, journal, approver)
    pipe.execute(shell("ls /tmp", llm_risk=RiskLevel.SENSITIVE), ctx)
    assert len(approver.requests) == 1  # raised from SAFE
    approver2 = FakeApprover()
    result = make_pipeline(registry, journal, approver2).execute(
        shell("sudo reboot", llm_risk=RiskLevel.SAFE), ctx
    )
    assert result.declined  # LLM saying "safe" did not help (default approver denies)


def test_always_allow_skips_second_prompt(registry, journal, ctx):
    approver = FakeApprover(ApprovalChoice.ALWAYS)
    pipe = make_pipeline(registry, journal, approver)
    pipe.execute(shell("sudo apt update"), ctx)
    pipe.execute(shell("sudo apt update"), ctx)
    assert len(approver.requests) == 1


def test_always_is_ignored_for_irreversible_steps(registry, journal, ctx):
    approver = FakeApprover(ApprovalChoice.ALWAYS, ApprovalChoice.NO)
    pipe = make_pipeline(registry, journal, approver)
    pipe.execute(shell("rm -r /tmp/x"), ctx)
    second = pipe.execute(shell("rm -r /tmp/x"), ctx)
    assert len(approver.requests) == 2 and second.declined


def test_edited_command_is_reclassified(registry, journal, ctx):
    edit = ApprovalResponse(ApprovalChoice.EDIT, "rm -rf /")
    approver = FakeApprover(edit)
    result = make_pipeline(registry, journal, approver).execute(shell("sudo apt update"), ctx)
    assert result.refused  # the edit turned it into a hard-blocked command
    assert spy(registry).ran == []


def test_edit_not_supported_for_typed_tools(registry, journal, ctx, tmp_path):
    ctx.mode = Mode.ASK_ALL
    approver = FakeApprover(ApprovalResponse(ApprovalChoice.EDIT, "whatever"))
    result = make_pipeline(registry, journal, approver).execute(
        ToolCall("create_folder", {"path": str(tmp_path / "x")}), ctx
    )
    assert result.declined and not (tmp_path / "x").exists()


def test_journal_and_audit_record_everything(registry, journal, ctx, tmp_path):
    pipe = make_pipeline(registry, journal, FakeApprover(ApprovalChoice.NO))
    pipe.execute(ToolCall("list_files", {"path": str(tmp_path)}), ctx)
    pipe.execute(shell("sudo apt update"), ctx)  # declined: audited, not journaled
    assert [e.tool for e in journal.entries] == ["list_files"]
    assert journal.entries[0].ok is True
    decisions = [a for a in journal.audit_log if a["event"] == "decision"]
    assert [d["action"] for d in decisions] == ["allow", "ask"]


def test_tool_exception_does_not_crash(registry, journal, ctx):
    class Boom(type(registry.get("list_files"))):
        def run(self, args, ctx):
            raise RuntimeError("boom")

    registry.register(Boom())
    crashed = make_pipeline(registry, journal).execute(ToolCall("list_files", {"path": "."}), ctx)
    assert not crashed.ok and "boom" in crashed.error
