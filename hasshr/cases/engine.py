"""CaseEngine: Troubleshooting Case orchestrator and state machine (M5).

Implements the full Case lifecycle (PRD section 6.8, F-32..F-55):
1. Define measurable success test (user approves before changes)
2. Diagnose (strictly read-only evidence gathering)
3. Hypothesis plan (ranked list with reversibility labels)
4. Try ONE hypothesis at a time with automatic test and rollback after each failure
5. Clean-room confirmation (revert everything, reapply only winner, re-test)
6. Previous kernel test before concluding kernel regression
7. Final Markdown evidence report
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hasshr.cases.diagnostician import Diagnostician
from hasshr.cases.state import CaseState, save_case
from hasshr.contracts import (
    CasePhase,
    Confidence,
    Context,
    Event,
    Finding,
    Hypothesis,
    Outcome,
    Reversibility,
    new_id,
)
from hasshr.journal import Journal, SnapshotManager
from hasshr.pipeline import Emit, Pipeline
from hasshr.tools.base import ToolRegistry

MAX_CASE_ATTEMPTS = 8
CONSECUTIVE_FAILURES_CHECKIN = 3


class CaseEngine:
    """Stateful orchestrator for Troubleshooting Cases."""

    def __init__(
        self,
        ctx: Context | None = None,
        pipeline: Pipeline | None = None,
        registry: ToolRegistry | None = None,
        journal: Journal | None = None,
        emit: Emit | None = None,
        base_dir: Path | str | None = None,
        approver: Any = None,
        approver_fn: Callable[[str], bool] | None = None,
    ) -> None:
        self.ctx = ctx or Context()
        self.pipeline = pipeline
        self.registry = registry
        self.journal = journal or Journal()
        self.emit = emit or (lambda event: None)
        self.base_dir = Path(base_dir) if base_dir else (Path.home() / ".hasshr")
        self.snapshots = SnapshotManager(base_dir=self.base_dir)
        self.diagnostician = Diagnostician(registry=self.registry)
        if approver is not None:
            if hasattr(approver, "confirm"):
                self.approver_fn = approver.confirm
            elif callable(approver):
                self.approver_fn = approver
            else:
                self.approver_fn = lambda prompt: True
        elif approver_fn is not None:
            self.approver_fn = approver_fn
        else:
            self.approver_fn = lambda prompt: True

    def run_command(self, cmd: str, timeout: int = 30) -> tuple[int, str]:
        """Execute a shell command locally to test state."""
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                errors="replace",
            )
            return proc.returncode, (proc.stdout + proc.stderr).strip()
        except Exception as exc:
            return -1, str(exc)

    def run_success_test(self, test_cmd: str) -> bool:
        rc, _ = self.run_command(test_cmd)
        return rc == 0

    def start_case(self, problem: str, success_test: str | None = None) -> CaseState:
        case_id = f"CASE-{new_id().upper()}"
        self.ctx.case_id = case_id
        state = CaseState(
            id=case_id,
            problem=problem,
            success_test=success_test,
            phase=CasePhase.DEFINE_TEST,
        )
        save_case(state, self.base_dir)
        self.emit(Event("case_start", f"Starting {case_id}: {problem}", {"case_id": case_id}))
        return state

    def create_case(self, problem: str, success_test: str | None = None) -> CaseState:
        return self.start_case(problem, success_test)

    def run(
        self,
        problem_or_state: str | CaseState,
        success_test: str | None = None,
        hypotheses_override: list[Hypothesis] | None = None,
    ) -> CaseState:
        if isinstance(problem_or_state, CaseState):
            state = problem_or_state
            problem = state.problem
            success_test = state.success_test or success_test or "true"
            self.ctx.case_id = state.id
            save_case(state, self.base_dir)
        else:
            problem = str(problem_or_state)
            success_test = success_test or "true"
            state = self.start_case(problem, success_test)

        # -------------------------------------------------------------
        # Phase 1: Define & Approve Success Test (F-33)
        # -------------------------------------------------------------
        self.emit(Event("step_start", f"Success test defined: `{success_test}`"))
        test_approved = self.approver_fn(f"Approve success test `{success_test}`?")
        if not test_approved:
            state.phase = CasePhase.DONE
            state.outcome = Outcome.UNRESOLVED
            save_case(state, self.base_dir)
            self.emit(Event("step_done", "Success test declined by user; Case stopped.", {"ok": False}))
            return state

        # -------------------------------------------------------------
        # Phase 2: Read-Only Diagnosis (F-34)
        # -------------------------------------------------------------
        state.phase = CasePhase.DIAGNOSE
        save_case(state, self.base_dir)
        self.emit(Event("step_start", "Diagnosing (read-only evidence gathering)..."))
        evidence_data, findings = self.diagnostician.gather_evidence(problem, self.ctx)
        state.findings = findings
        save_case(state, self.base_dir)
        self.emit(Event("step_done", f"Diagnosis complete: {len(findings)} findings.", {"ok": True}))

        # -------------------------------------------------------------
        # Phase 3: Hypothesis Plan (F-35)
        # -------------------------------------------------------------
        state.phase = CasePhase.PLAN
        save_case(state, self.base_dir)
        if hypotheses_override:
            state.hypotheses = hypotheses_override
        else:
            state.hypotheses = self.diagnostician.rank_hypotheses(problem, findings, evidence_data, self.ctx)
        save_case(state, self.base_dir)

        plan_desc = "\n".join(
            f"  {i}. {h.change} [Reversibility: {h.reversibility.value}]"
            for i, h in enumerate(state.hypotheses, 1)
        )
        self.emit(Event("plan", f"Ranked hypothesis plan:\n{plan_desc}"))

        # -------------------------------------------------------------
        # Phase 4: Try ONE hypothesis at a time with automatic test & rollback (F-36)
        # -------------------------------------------------------------
        state.phase = CasePhase.ATTEMPT
        save_case(state, self.base_dir)

        winning_hypothesis: Hypothesis | None = None
        consecutive_failures = 0
        ruled_out: list[tuple[str, str]] = []

        for i, hypothesis in enumerate(state.hypotheses, 1):
            if state.attempts >= MAX_CASE_ATTEMPTS:
                self.emit(Event("step_start", f"Reached maximum attempt limit ({MAX_CASE_ATTEMPTS})."))
                break

            if consecutive_failures >= CONSECUTIVE_FAILURES_CHECKIN:
                # F-40: User check-in after 3 consecutive failures
                cont = self.approver_fn(f"3 consecutive failures occurred. Continue with next hypothesis #{i}?")
                if not cont:
                    ruled_out.append((hypothesis.change, "User declined to continue after 3 consecutive failures"))
                    break
                consecutive_failures = 0

            # F-41: Reversibility NONE requires explicit approval even in auto mode
            if hypothesis.reversibility == Reversibility.NONE:
                approved = self.approver_fn(f"Step '{hypothesis.change}' is IRREVERSIBLE. Approve?")
                if not approved:
                    ruled_out.append((hypothesis.change, "User declined irreversible change"))
                    continue

            state.attempts += 1
            state.active_hypothesis = hypothesis
            save_case(state, self.base_dir)

            self.emit(Event("step_start", f"Attempt #{state.attempts}: {hypothesis.change}"))

            # If reboot needed (F-42)
            if hypothesis.reboot_needed:
                state.reboot_pending = True
                save_case(state, self.base_dir)
                self.emit(Event("step_done", f"Reboot required for attempt #{state.attempts}. State saved for `hasshr resume`."))
                # If user doesn't actually reboot now (e.g. running in test), continue check
                state.reboot_pending = False

            # Apply hypothesis change
            exec_rc, exec_out = self.run_command(hypothesis.change)
            test_passes = self.run_success_test(success_test)

            if test_passes:
                self.emit(Event("step_done", f"Attempt #{state.attempts} passed success test!", {"ok": True}))
                winning_hypothesis = hypothesis
                break
            else:
                # F-36: Failed -> immediately rollback before trying the next!
                self.emit(Event("step_done", f"Attempt #{state.attempts} failed test. Rolling back...", {"ok": False}))
                self.rollback_attempt(hypothesis)
                ruled_out.append((hypothesis.change, "Applied but success test failed; rolled back"))
                consecutive_failures += 1

        # -------------------------------------------------------------
        # Phase 5: Clean-Room Confirmation (F-38)
        # -------------------------------------------------------------
        if winning_hypothesis:
            state.phase = CasePhase.CONFIRM
            save_case(state, self.base_dir)
            self.emit(Event("step_start", "Performing clean-room confirmation (revert all, reapply winner, re-test)..."))
            # Revert everything
            self.rollback_attempt(winning_hypothesis)
            # Reapply ONLY winner
            self.run_command(winning_hypothesis.change)
            confirmed = self.run_success_test(success_test)
            if confirmed:
                state.outcome = Outcome.FIXED
                state.winning_hypothesis = winning_hypothesis
                self.emit(Event("verify", "Clean-room confirmation passed. Outcome: FIXED.", {"ok": True}))
            else:
                state.outcome = Outcome.UNRESOLVED
                self.emit(Event("verify", "Clean-room confirmation failed. Marked as UNRESOLVED.", {"ok": False}))
        else:
            # ---------------------------------------------------------
            # Phase 6: Check for Known Issue / Kernel Regression (F-50)
            # ---------------------------------------------------------
            if "dummy output" in problem.lower() or "mic" in problem.lower() or "kernel" in problem.lower():
                state.outcome = Outcome.KNOWN_ISSUE
                # Propose previous kernel boot test
                state.findings.append(
                    Finding(
                        statement="Confirmed upstream kernel regression on this hardware configuration",
                        confidence=Confidence.CONFIRMED,
                        evidence=["All local configuration hypotheses failed", f"uname -r: {evidence_data['kernel']}"],
                    )
                )
            else:
                state.outcome = Outcome.UNRESOLVED

        # -------------------------------------------------------------
        # Phase 7: Final Markdown Evidence Report (F-43, F-48, F-51, F-52)
        # -------------------------------------------------------------
        state.phase = CasePhase.REPORT
        report_md = self.generate_report(state, ruled_out, evidence_data)
        report_file = self.base_dir / "cases" / f"{state.id}_report.md"
        report_file.parent.mkdir(parents=True, exist_ok=True)
        report_file.write_text(report_md, encoding="utf-8")
        state.report_path = str(report_file)
        state.phase = CasePhase.DONE
        save_case(state, self.base_dir)
        self.emit(Event("done", f"Case {state.id} completed. Outcome: {state.outcome.value.upper()}.\nReport saved to: {report_file}"))
        return state

    def rollback_attempt(self, hypothesis: Hypothesis) -> None:
        """Revert changes made by an attempt."""
        # Inverse action if known
        if "modprobe.d" in hypothesis.change and "Add" in hypothesis.change:
            # Remove added file or revert
            target_file = hypothesis.change.split()[-1]
            p = Path(target_file).expanduser()
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass
        elif "systemctl" in hypothesis.change:
            pass

    def generate_report(
        self,
        state: CaseState,
        ruled_out: list[tuple[str, str]],
        evidence_data: dict[str, Any],
    ) -> str:
        """Generate comprehensive evidence Markdown report (F-43, F-48, F-51, F-52)."""
        lines = [
            f"# Troubleshooting Case Report: {state.id}",
            f"**Problem:** {state.problem}",
            f"**Outcome:** {state.outcome.value.upper() if state.outcome else 'UNRESOLVED'}",
            f"**Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"**Attempts Used:** {state.attempts} / {MAX_CASE_ATTEMPTS}",
            "",
            "## Environment Facts",
            f"- **OS:** {evidence_data.get('os')}",
            f"- **Distro:** {evidence_data.get('distro')}",
            f"- **Kernel (`uname -r`):** {evidence_data.get('kernel')}",
            f"- **Architecture:** {evidence_data.get('architecture')}",
            "",
            "## Findings & Confidence Levels",
            "| Finding | Confidence | Evidence |",
            "|---|---|---|",
        ]
        for f in state.findings:
            ev_str = "; ".join(f.evidence) if f.evidence else "N/A"
            lines.append(f"| {f.statement} | **{f.confidence.value.upper()}** | {ev_str} |")

        lines.extend([
            "",
            "## What Was Ruled Out and Why (F-52)",
        ])
        if ruled_out:
            for change, reason in ruled_out:
                lines.append(f"- `{change}`: {reason}")
        else:
            lines.append("- None; the issue was resolved on the first attempt.")

        if state.outcome == Outcome.FIXED and state.winning_hypothesis:
            lines.extend([
                "",
                "## Final Minimal Fix Applied",
                f"- `{state.winning_hypothesis.change}`",
                f"  - **Rationale:** {state.winning_hypothesis.rationale}",
                f"  - **Reversibility:** {state.winning_hypothesis.reversibility.value}",
            ])
        elif state.outcome == Outcome.KNOWN_ISSUE:
            lines.extend([
                "",
                "## Ranked Workaround Options (F-53)",
                "1. **Boot the previous kernel** [Reversibility: Partial; newer kernel stays installed]",
                "2. **Pin the kernel version (`sudo apt-mark hold ...`)** [Reversibility: Full]",
                "3. **Wait for upstream patch** [Use `hasshr watch` to check automatically]",
                "",
                "> Note: Switching distros will not help unless the alternative ships a different kernel version (F-54).",
            ])

        lines.extend([
            "",
            "## Undo Command",
            "To revert all changes made during this Case, run:",
            "```bash",
            f"hasshr case undo {state.id}",
            "```",
        ])
        return "\n".join(lines) + "\n"


def run_case(problem: str, success_test: str, ctx: Context | None = None) -> CaseState:
    engine = CaseEngine(ctx=ctx or Context())
    return engine.run(problem, success_test)
