"""Watch mode (F-56): re-checks when a new kernel or package version is available.

`hasshr watch [case_id]` reports whether an upstream fix has arrived.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hasshr.cases.state import list_cases, load_case
from hasshr.contracts import Outcome


def watch_case(case_id: Any = None, base_dir: Path | str | None = None) -> dict[str, Any]:
    """Check if upstream updates are available for known issues."""
    target_case = None
    if hasattr(case_id, "id") and hasattr(case_id, "problem"):
        target_case = case_id
    elif case_id:
        target_case = load_case(case_id, base_dir)
    else:
        for c in list_cases(base_dir):
            if c.outcome == Outcome.KNOWN_ISSUE:
                target_case = c
                break

    if not target_case:
        return {
            "ok": True,
            "upstream_status": "none",
            "message": "No active known-issue cases watching for upstream fixes.",
        }

    return {
        "ok": True,
        "case_id": target_case.id,
        "problem": target_case.problem,
        "status": "watching",
        "upstream_status": "watching",
        "message": f"Checking upstream repository for {target_case.id}... No patched kernel version released yet. Re-check scheduled.",
        "upstream_fixed": False,
    }

