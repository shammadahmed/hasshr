"""Troubleshooting Cases module (M5).

Stateful, guided diagnosis and trial-and-error problem solving with rollback
and clean-room confirmation (PRD section 6.8).
"""

from termiai.contracts import Confidence, Finding, Hypothesis

from .diagnostician import Diagnostician, EvidenceCollector
from .engine import CaseEngine, run_case
from .resume import resume_case
from .state import (
    CaseState,
    delete_case_state,
    get_active_case,
    list_cases,
    list_saved_cases,
    load_case,
    load_case_state,
    save_case,
    save_case_state,
)
from .undo import undo_case
from .watch import watch_case

__all__ = [
    "CaseEngine",
    "CaseState",
    "Confidence",
    "Diagnostician",
    "EvidenceCollector",
    "Finding",
    "Hypothesis",
    "delete_case_state",
    "list_saved_cases",
    "load_case_state",
    "resume_case",
    "run_case",
    "save_case_state",
    "undo_case",
    "watch_case",
    "save_case",
    "load_case",
    "list_cases",
    "get_active_case",
]
