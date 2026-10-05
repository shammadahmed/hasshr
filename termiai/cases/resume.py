"""Resume a Case after reboot (F-42).

`termiai resume` runs the success test and continues or rolls back.
"""

from __future__ import annotations

from pathlib import Path

from termiai.cases.engine import CaseEngine
from termiai.cases.state import CaseState, get_active_case, load_case, save_case
from termiai.contracts import CasePhase, Context, Outcome


def resume_case(case_id: str | None = None, base_dir: Path | None = None) -> CaseState | None:
    """Resume an in-progress case that was waiting for a reboot."""
    state = load_case(case_id, base_dir) if case_id else get_active_case(base_dir)
    if not state:
        return None

    if not state.success_test:
        return state

    engine = CaseEngine(ctx=Context(case_id=state.id), base_dir=base_dir)
    # Run the success test
    passes = engine.run_success_test(state.success_test)

    if passes:
        state.reboot_pending = False
        # Proceed to clean-room confirmation
        state.phase = CasePhase.CONFIRM
        save_case(state, base_dir)
        if state.active_hypothesis:
            state.winning_hypothesis = state.active_hypothesis
        state.outcome = Outcome.FIXED
        state.phase = CasePhase.DONE
        save_case(state, base_dir)
    else:
        # Failed: roll back the attempt that requested reboot
        if state.active_hypothesis:
            engine.rollback_attempt(state.active_hypothesis)
        state.reboot_pending = False
        # Continue to next hypothesis if available
        remaining = [h for h in state.hypotheses if h != state.active_hypothesis]
        if remaining:
            return engine.run(state.problem, state.success_test, hypotheses_override=remaining)
        else:
            state.outcome = Outcome.UNRESOLVED
            state.phase = CasePhase.DONE
            save_case(state, base_dir)
    return state

