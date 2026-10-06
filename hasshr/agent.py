"""Orchestrator: Plan -> Execute -> Verify -> (replan, max retries) (M1)."""

from __future__ import annotations

from hasshr.agents import Executor, Planner, StepBudget, Verifier
from hasshr.contracts import Context, Event, LLMClient, RunReport
from hasshr.pipeline import Emit, Pipeline
from hasshr.tools.base import ToolRegistry


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        pipeline: Pipeline,
        ctx: Context,
        emit: Emit | None = None,
        max_steps: int = 25,
        max_retries: int = 3,
    ) -> None:
        self.ctx = ctx
        self.emit: Emit = emit or (lambda event: None)
        self.max_steps, self.max_retries = max_steps, max_retries
        self.planner = Planner(llm)
        self.executor = Executor(llm, pipeline, registry, ctx)
        self.verifier = Verifier(llm, pipeline, registry, ctx)

    def run(self, goal: str) -> RunReport:
        budget = StepBudget(self.max_steps)
        plan = self.planner.plan(goal, self.ctx)
        self.emit(Event("plan", "; ".join(s.description for s in plan.steps)))
        attempts = 0
        while True:
            attempts += 1
            results = []
            for i, step in enumerate(plan.steps, 1):
                self.emit(Event("step_start", f"[{i}/{len(plan.steps)}] {step.description}"))
                sr = self.executor.run_step(goal, step, budget)
                results.append(sr)
                self.emit(Event("step_done", sr.summary, {"ok": sr.ok}))
                if sr.declined or sr.refused:
                    why = (
                        "an action was declined by the user"
                        if sr.declined
                        else ("an action was refused by the safety engine")
                    )
                    return self._done(
                        RunReport(False, sr.summary, attempts, plan, None, why, budget.used)
                    )
                if sr.budget_exhausted:
                    return self._done(
                        RunReport(
                            False,
                            "Stopped: step limit reached.",
                            attempts,
                            plan,
                            None,
                            "step limit reached",
                            budget.used,
                        )
                    )
                if not sr.ok:
                    break  # let the Verifier confirm, then replan
            verdict = self.verifier.verify(goal, plan, results, budget)
            self.emit(Event("verify", verdict.notes, {"ok": verdict.ok}))
            if verdict.ok:
                return self._done(
                    RunReport(True, verdict.notes, attempts, plan, verdict, None, budget.used)
                )
            if budget.exhausted:
                return self._done(
                    RunReport(
                        False,
                        "Stopped: step limit reached.",
                        attempts,
                        plan,
                        verdict,
                        "step limit reached",
                        budget.used,
                    )
                )
            if attempts > self.max_retries:
                return self._done(
                    RunReport(
                        False,
                        verdict.notes,
                        attempts,
                        plan,
                        verdict,
                        f"not verified after {attempts} attempts",
                        budget.used,
                    )
                )
            plan = self.planner.plan(goal, self.ctx, feedback=verdict.notes, previous=plan)
            self.emit(Event("replan", "; ".join(s.description for s in plan.steps)))

    def _done(self, report: RunReport) -> RunReport:
        self.emit(Event("done", report.summary, {"ok": report.ok}))
        return report
