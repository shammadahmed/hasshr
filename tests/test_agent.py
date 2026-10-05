from __future__ import annotations

from conftest import FakeApprover, make_pipeline

from termiai.agent import Agent
from termiai.contracts import (
    ApprovalChoice,
    Context,
    LLMResponse,
    Mode,
    ToolCall,
)
from termiai.llm import ScriptedLLM, demo_script, reply

PLAN_ONE = '{"steps": [{"description": "make the folder"}]}'
OK = '{"ok": true, "notes": "all good"}'
NOT_OK = '{"ok": false, "notes": "folder is missing"}'


def build(registry, journal, llm, mode=Mode.AUTO, approver=None, **kw):
    ctx = Context(mode=mode)
    pipe = make_pipeline(registry, journal, approver)
    return Agent(llm, registry, pipe, ctx, **kw), ctx


def mk(path) -> ToolCall:
    return ToolCall("create_folder", {"path": str(path)}, explanation="make it")


def test_happy_path(registry, journal, tmp_path):
    llm = ScriptedLLM([reply(PLAN_ONE), reply("", mk(tmp_path / "PDFs")), reply("done"), reply(OK)])
    agent, _ = build(registry, journal, llm)
    report = agent.run("make a PDFs folder")
    assert report.ok and report.attempts == 1 and (tmp_path / "PDFs").is_dir()
    assert (
        report.summary == "all good" and report.steps_used == 3
    )  # executor x2 + verifier; planner is bounded by retries


def test_verify_fail_replans_then_succeeds(registry, journal, tmp_path):
    llm = ScriptedLLM(
        [
            reply(PLAN_ONE),
            reply("did nothing"),
            reply(NOT_OK),  # attempt 1 fails
            reply(PLAN_ONE),  # replan
            reply("", mk(tmp_path / "x")),
            reply("done"),
            reply(OK),  # attempt 2 works
        ]
    )
    agent, _ = build(registry, journal, llm)
    report = agent.run("make x")
    assert report.ok and report.attempts == 2 and (tmp_path / "x").is_dir()
    replan_prompt = llm.calls[3][0][1]["content"]
    assert "folder is missing" in replan_prompt  # verifier feedback reached the planner


def test_gives_up_after_max_retries(registry, journal):
    # plan once up-front, then (verify-fail, replan) x3, then final verify-fail
    llm = ScriptedLLM(
        [reply(PLAN_ONE)]
        + [reply("nothing"), reply(NOT_OK), reply(PLAN_ONE)] * 3
        + [reply("nothing"), reply(NOT_OK)]
    )
    agent, _ = build(registry, journal, llm, max_retries=3)
    report = agent.run("impossible")
    assert not report.ok and report.attempts == 4
    assert "not verified after 4 attempts" in report.stopped_reason


def test_declined_action_stops_without_retry(registry, journal, tmp_path):
    llm = ScriptedLLM([reply(PLAN_ONE), reply("", mk(tmp_path / "x"))])
    agent, _ = build(
        registry, journal, llm, mode=Mode.ASK_ALL, approver=FakeApprover(ApprovalChoice.NO)
    )
    report = agent.run("make x")
    assert not report.ok and "declined" in report.stopped_reason
    assert not (tmp_path / "x").exists() and len(llm.calls) == 2  # no verify, no replan


def test_refused_action_stops(registry, journal):
    call = ToolCall("run_shell", {"command": "rm -rf /"})
    llm = ScriptedLLM([reply(PLAN_ONE), reply("", call)])
    agent, _ = build(registry, journal, llm)
    report = agent.run("wipe everything")
    assert not report.ok and "refused" in report.stopped_reason
    assert registry.get("run_shell").ran == []


def test_step_limit(registry, journal, tmp_path):
    def forever(messages, tools):
        return LLMResponse("", [ToolCall("list_files", {"path": str(tmp_path)})])

    llm = ScriptedLLM([reply(PLAN_ONE)] + [forever] * 10)
    agent, _ = build(registry, journal, llm, max_steps=3)
    report = agent.run("loop")
    assert not report.ok and report.stopped_reason == "step limit reached"
    assert report.steps_used == 3


def test_bad_plan_json_falls_back_to_single_step(registry, journal):
    llm = ScriptedLLM([reply("sorry, no JSON here"), reply("handled"), reply(OK)])
    agent, _ = build(registry, journal, llm)
    report = agent.run("do the thing")
    assert report.ok and [s.description for s in report.plan.steps] == ["do the thing"]


def test_verifier_cannot_use_mutating_tools(registry, journal, tmp_path):
    sneaky = ToolCall("create_folder", {"path": str(tmp_path / "sneaky")})
    llm = ScriptedLLM([reply(PLAN_ONE), reply("noop"), reply("", sneaky), reply(OK)])
    agent, _ = build(registry, journal, llm)
    report = agent.run("check only")
    assert report.ok and not (tmp_path / "sneaky").exists()
    verifier_tools = [t["function"]["name"] for t in llm.calls[2][1]]
    assert "list_files" in verifier_tools and "create_folder" not in verifier_tools
    assert all(registry.get(t).read_only for t in verifier_tools)  # only read-only tools are offered


def test_unparseable_verdict_is_not_success(registry, journal):
    llm = ScriptedLLM([reply(PLAN_ONE), reply("noop"), reply("looks fine to me!")])
    agent, _ = build(registry, journal, llm, max_retries=0)
    report = agent.run("x")
    assert not report.ok and report.verdict is not None and not report.verdict.parseable


def test_events_are_emitted(registry, journal, tmp_path):
    events = []
    llm = ScriptedLLM([reply(PLAN_ONE), reply("", mk(tmp_path / "e")), reply("done"), reply(OK)])
    agent, _ = build(registry, journal, llm, emit=events.append)
    agent.run("make e")
    kinds = [e.kind for e in events]
    assert kinds[0] == "plan" and kinds[-1] == "done" and "verify" in kinds


def test_demo_script_walking_skeleton(registry, journal, tmp_path):
    (tmp_path / "a.pdf").write_text("x")
    agent, _ = build(registry, journal, demo_script(str(tmp_path)), mode=Mode.ASK_SENSITIVE)
    report = agent.run("list my Downloads")
    assert report.ok
