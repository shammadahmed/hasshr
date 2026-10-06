"""CaseState machine and persistence for Troubleshooting Cases (M5).

State survives reboots on disk so `hasshr resume` can continue (F-42).
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hasshr.contracts import (
    CasePhase,
    CaseState,
    Confidence,
    Finding,
    Hypothesis,
    Outcome,
    Reversibility,
    new_id,
)

DEFAULT_CASES_DIR = Path.home() / ".hasshr" / "cases"


def _cases_dir(base_dir: str | Path | None = None) -> Path:
    if base_dir:
        p = Path(base_dir)
        return p if p.name == "cases" else p / "cases"
    return DEFAULT_CASES_DIR


def serialize_state(state: CaseState) -> dict[str, Any]:
    return {
        "id": state.id,
        "problem": state.problem,
        "success_test": state.success_test,
        "phase": state.phase.value if hasattr(state.phase, "value") else str(state.phase),
        "hypotheses": [
            {
                "change": h.change,
                "rationale": h.rationale,
                "reversibility": h.reversibility.value if hasattr(h.reversibility, "value") else str(h.reversibility),
                "reboot_needed": h.reboot_needed,
                "source": h.source,
            }
            for h in state.hypotheses
        ],
        "attempts": state.attempts,
        "findings": [
            {
                "statement": f.statement,
                "confidence": f.confidence.value if hasattr(f.confidence, "value") else str(f.confidence),
                "evidence": f.evidence,
            }
            for f in state.findings
        ],
        "outcome": state.outcome.value if state.outcome and hasattr(state.outcome, "value") else (str(state.outcome) if state.outcome else None),
        "active_hypothesis": asdict(state.active_hypothesis) if state.active_hypothesis else None,
        "winning_hypothesis": asdict(state.winning_hypothesis) if state.winning_hypothesis else None,
        "history": state.history,
        "reboot_pending": state.reboot_pending,
        "report_path": state.report_path,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def deserialize_state(data: dict[str, Any]) -> CaseState:
    phase_val = data.get("phase", CasePhase.DEFINE_TEST.value)
    try:
        phase = CasePhase(phase_val)
    except ValueError:
        phase = CasePhase.DEFINE_TEST

    outcome_val = data.get("outcome")
    outcome = None
    if outcome_val:
        try:
            outcome = Outcome(outcome_val)
        except ValueError:
            outcome = None

    hypotheses = [
        Hypothesis(
            change=h["change"],
            rationale=h.get("rationale", ""),
            reversibility=Reversibility(h.get("reversibility", Reversibility.FULL.value)),
            reboot_needed=h.get("reboot_needed", False),
            source=h.get("source", "own reasoning"),
        )
        for h in data.get("hypotheses", [])
    ]

    findings = [
        Finding(
            statement=f["statement"],
            confidence=Confidence(f.get("confidence", Confidence.SPECULATION.value)),
            evidence=f.get("evidence", []),
        )
        for f in data.get("findings", [])
    ]

    return CaseState(
        id=data.get("id", new_id()),
        problem=data.get("problem", ""),
        success_test=data.get("success_test"),
        phase=phase,
        hypotheses=hypotheses,
        attempts=data.get("attempts", 0),
        findings=findings,
        outcome=outcome,
        active_hypothesis=Hypothesis(**data["active_hypothesis"]) if data.get("active_hypothesis") else None,
        winning_hypothesis=Hypothesis(**data["winning_hypothesis"]) if data.get("winning_hypothesis") else None,
        history=data.get("history", []),
        reboot_pending=data.get("reboot_pending", False),
        report_path=data.get("report_path"),
        created_at=data.get("created_at", time.time()),
        updated_at=data.get("updated_at", time.time()),
    )


def save_case(state: CaseState, base_dir: str | Path | None = None) -> Path:
    root = _cases_dir(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    state.updated_at = time.time()
    path = root / f"{state.id}.json"
    path.write_text(json.dumps(serialize_state(state), indent=2), encoding="utf-8")
    return path


def load_case(case_id: str, base_dir: str | Path | None = None) -> CaseState | None:
    root = _cases_dir(base_dir)
    path = root / f"{case_id}.json"
    if not path.exists() and base_dir:
        alt = Path(base_dir) / f"{case_id}.json"
        if alt.exists():
            path = alt
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return deserialize_state(data)
    except (OSError, json.JSONDecodeError):
        return None


def list_cases(base_dir: str | Path | None = None) -> list[CaseState]:
    root = _cases_dir(base_dir)
    if not root.exists():
        return []
    cases = []
    for p in sorted(root.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            cases.append(deserialize_state(data))
        except Exception:
            continue
    return cases


def get_active_case(base_dir: str | Path | None = None) -> CaseState | None:
    for c in list_cases(base_dir):
        if c.reboot_pending or c.phase not in (CasePhase.DONE, CasePhase.REPORT):
            return c
    return None


def delete_case_state(case_id: str, base_dir: str | Path | None = None) -> bool:
    root = _cases_dir(base_dir)
    path = root / f"{case_id}.json"
    if not path.exists() and base_dir:
        alt = Path(base_dir) / f"{case_id}.json"
        if alt.exists():
            path = alt
    if path.exists():
        try:
            path.unlink()
            return True
        except OSError:
            return False
    return False


save_case_state = save_case
load_case_state = load_case
list_saved_cases = list_cases
