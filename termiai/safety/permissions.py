"""Permission decisions for TermiAI (Member 2)."""
from __future__ import annotations

from termiai.contracts import Action, Context, Decision, Mode, Reversibility, RiskLevel, RiskResult


def decide(
    risk: RiskResult,
    mode: Mode,
    ctx: Context,
    reversibility: Reversibility = Reversibility.FULL,
) -> Decision:
    """Decide whether to allow, ask the user, or refuse a proposed action.

    Hard blocks are unconditional. ASK_ALL asks for every non-blocked action.
    Irreversible actions require approval even in AUTO mode. ASK_SENSITIVE allows
    read-only and moderate actions, but asks before sensitive actions.
    """
    if risk.level == RiskLevel.BLOCKED:
        return Decision(Action.REFUSE, mode, "hard-blocked in every mode")

    if mode == Mode.PLAN_ONLY:
        return Decision(Action.PLAN, mode, "plan-only mode does not execute commands")

    if risk.level == RiskLevel.SAFE:
        return Decision(Action.ALLOW, mode, "classified as read-only")

    if mode == Mode.ASK_ALL:
        return Decision(Action.ASK, mode, "ask-all mode requires approval for every action")

    if reversibility == Reversibility.NONE:
        return Decision(Action.ASK, mode, "irreversible step requires explicit approval")

    if mode == Mode.AUTO:
        return Decision(Action.ALLOW, mode, "auto mode allows non-blocked, non-irreversible actions")

    if risk.level == RiskLevel.SENSITIVE:
        return Decision(Action.ASK, mode, "sensitive action requires approval in ask-sensitive mode")

    if risk.level == RiskLevel.MODERATE:
        if reversibility == Reversibility.PARTIAL:
            return Decision(Action.ASK, mode, "partially reversible change requires approval")
        return Decision(Action.ALLOW, mode, "moderate, reversible action in ask-sensitive mode")

    # Unknown enum values or malformed risk objects must not silently allow.
    return Decision(Action.ASK, mode, "unrecognized risk state; defaulting to approval")

