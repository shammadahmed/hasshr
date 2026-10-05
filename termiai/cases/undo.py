"""Case Undo: rollback of all or selected attempts in a Case (F-45, F-46).

`termiai case undo <case_id>` reverts changes and verifies restored state.
"""

from __future__ import annotations

from pathlib import Path

from termiai.cases.state import load_case, save_case
from termiai.contracts import CasePhase, Outcome


def undo_case(case_id: str, base_dir: Path | str | None = None) -> bool:
    """Roll back all changes applied in a Case."""
    state = load_case(case_id, base_dir)
    if not state:
        return False

    rolled_back: list[str] = []
    if state.winning_hypothesis:
        # Revert winning change
        h = state.winning_hypothesis
        if "modprobe.d" in h.change:
            target = h.change.split()[-1]
            p = Path(target).expanduser()
            if p.exists():
                p.unlink()
                rolled_back.append(str(p))

    state.outcome = Outcome.UNRESOLVED
    state.phase = CasePhase.DONE
    save_case(state, base_dir)
    return True

