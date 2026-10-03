"""STUB: owned by M2 (Safety engine). Replace the body, keep the signature.

decide(risk, mode, ctx, reversibility) -> Decision
"""

from __future__ import annotations

from termiai.contracts import (
    Action,
    Context,
    Decision,
    Mode,
    Reversibility,
    RiskLevel,
    RiskResult,
)


def decide(
    risk: RiskResult,
    mode: Mode,
    ctx: Context,
    reversibility: Reversibility = Reversibility.FULL,
) -> Decision:
    if risk.level == RiskLevel.BLOCKED:
        return Decision(Action.REFUSE, mode, "hard-blocked in every mode")
    if risk.level == RiskLevel.SAFE:
        return Decision(Action.ALLOW, mode, "read-only")
    # Irreversible steps always need explicit approval, even in auto mode (PRD F-41).
    if reversibility == Reversibility.NONE:
        return Decision(Action.ASK, mode, "irreversible step")
    if mode == Mode.AUTO:
        return Decision(Action.ALLOW, mode, "auto mode")
    if mode == Mode.ASK_ALL:
        return Decision(Action.ASK, mode, "ask-all mode")
    if risk.level >= RiskLevel.SENSITIVE:
        return Decision(Action.ASK, mode, "sensitive action")
    return Decision(Action.ALLOW, mode, "moderate action in ask-sensitive mode")
