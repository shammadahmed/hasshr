"""The execution pipeline (M1). NOTHING may run a tool except through Pipeline.execute().

classify -> decide -> (ask user) -> journal.before -> tool -> journal.after
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hasshr.contracts import (
    Action,
    ApprovalChoice,
    ApprovalRequest,
    ApprovalResponse,
    Approver,
    Context,
    Event,
    Reversibility,
    RiskLevel,
    RiskResult,
    ToolCall,
    ToolResult,
)
from hasshr.journal import Journal
from hasshr.safety import classify as default_classify
from hasshr.safety import decide as default_decide
from hasshr.tools.base import Tool, ToolRegistry

Emit = Callable[[Event], None]
MAX_EDIT_DEPTH = 3


class DenyApprover:
    """Safe default: declines everything that needs approval."""

    def ask(self, req: ApprovalRequest) -> ApprovalResponse:
        return ApprovalResponse(ApprovalChoice.NO)


class Pipeline:
    def __init__(
        self,
        registry: ToolRegistry,
        journal: Journal,
        approver: Approver | None = None,
        emit: Emit | None = None,
        classify: Callable[..., RiskResult] = default_classify,
        decide: Callable[..., Any] = default_decide,
    ) -> None:
        self.registry = registry
        self.journal = journal
        self.approver: Approver = approver or DenyApprover()
        self.emit: Emit = emit or (lambda event: None)
        self._classify = classify
        self._decide = decide

    # ------------------------------------------------------------------ risk
    def assess(self, tool: Tool, call: ToolCall, command: str, ctx: Context) -> RiskResult:
        """Deterministic rules first; the LLM's own rating can only RAISE the level."""
        base = tool.base_risk(call.args, ctx)
        if tool.shell_like:
            risk = self._classify(command, ctx)
            if base > risk.level:
                risk = RiskResult(base, risk.reasons + [f"{tool.name} base risk"], "rules")
        else:
            risk = RiskResult(base, [f"{tool.name} base risk"], "rules")
        if call.llm_risk is not None and call.llm_risk > risk.level:
            risk = RiskResult(call.llm_risk, risk.reasons + ["raised by LLM rating"], "llm")
        return risk

    # --------------------------------------------------------------- execute
    def execute(self, call: ToolCall, ctx: Context, _depth: int = 0) -> ToolResult:
        tool = self.registry.get(call.name)
        if tool is None:
            return ToolResult(ok=False, error=f"Unknown tool: {call.name}")

        command = tool.describe(call.args)
        risk = self.assess(tool, call, command, ctx)
        reversibility = tool.reversibility_for(call.args)
        decision = self._decide(risk, ctx.mode, ctx, reversibility)
        self.journal.audit(
            "decision",
            {
                "tool": call.name,
                "command": command,
                "risk": risk.level.name,
                "reasons": risk.reasons,
                "reversibility": reversibility.value,
                "action": decision.action.value,
                "mode": ctx.mode.value,
            },
        )
        self.emit(
            Event(
                "decision",
                f"{risk.level.name} -> {decision.action.value}",
                {"command": command, "risk": risk.level.name, "reversibility": reversibility.value},
            )
        )

        if decision.action == Action.REFUSE:
            return ToolResult(
                ok=False,
                refused=True,
                error=f"Refused ({', '.join(risk.reasons) or decision.reason})",
            )

        key = f"{call.name}:{command}"
        if decision.action == Action.ASK and key not in ctx.always_allow:
            resp = self.approver.ask(
                ApprovalRequest(call, command, risk, reversibility, call.explanation)
            )
            if resp.choice == ApprovalChoice.NO:
                return ToolResult(ok=False, declined=True, error="The user declined this action")
            if resp.choice == ApprovalChoice.EDIT:
                edited = (
                    tool.with_command(call, resp.edited_command) if resp.edited_command else None
                )
                if edited is None or _depth >= MAX_EDIT_DEPTH:
                    return ToolResult(ok=False, declined=True, error="Edit not possible; declined")
                # The edited command goes through classify/decide again, from scratch.
                return self.execute(edited, ctx, _depth + 1)
            if resp.choice == ApprovalChoice.ALWAYS and reversibility != Reversibility.NONE:
                ctx.always_allow.add(key)

        entry = self.journal.before(call, command, risk, reversibility, ctx)
        self.emit(Event("tool_start", call.explanation or command, {"tool": call.name}))
        try:
            result = tool.run(call.args, ctx)
        except Exception as exc:  # a crashing tool must never crash the agent
            result = ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        self.journal.after(entry, result)
        self.emit(
            Event(
                "tool_result",
                result.output[:200] if result.ok else (result.error or "failed"),
                {"tool": call.name, "ok": result.ok},
            )
        )
        return result


__all__ = ["DenyApprover", "Pipeline", "RiskLevel"]
