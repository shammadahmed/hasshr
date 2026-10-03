from __future__ import annotations

from termiai.contracts import (
    Context,
    LLMClient,
    PlanStep,
    StepResult,
    ToolCall,
    ToolResult,
    assistant_message,
)
from termiai.pipeline import Pipeline
from termiai.prompts import executor_system
from termiai.tools.base import ToolRegistry

MAX_TOOL_OUTPUT = 4000


class StepBudget:
    """Shared cap on Executor + Verifier LLM round-trips per run (PRD: default 25).

    Planner calls are not counted; they are bounded by max_retries instead.
    """

    def __init__(self, limit: int = 25) -> None:
        self.limit = limit
        self.used = 0

    def take(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True

    @property
    def exhausted(self) -> bool:
        return self.used >= self.limit


def render_result(result: ToolResult) -> str:
    if result.refused:
        return f"REFUSED by safety engine: {result.error}"
    if result.declined:
        return f"DECLINED by user: {result.error}"
    body = result.output if result.ok else f"ERROR: {result.error or result.output}"
    if len(body) > MAX_TOOL_OUTPUT:
        body = body[:MAX_TOOL_OUTPUT] + "\n... (truncated)"
    return body


class Executor:
    def __init__(
        self,
        llm: LLMClient,
        pipeline: Pipeline,
        registry: ToolRegistry,
        ctx: Context,
    ) -> None:
        self.llm, self.pipeline, self.registry, self.ctx = llm, pipeline, registry, ctx

    def run_step(self, goal: str, step: PlanStep, budget: StepBudget) -> StepResult:
        messages = [
            {"role": "system", "content": executor_system(self.ctx)},
            {
                "role": "user",
                "content": f"Overall goal: {goal}\nCURRENT STEP: {step.description}",
            },
        ]
        done: list[tuple[ToolCall, ToolResult]] = []
        schemas = self.registry.schemas()
        while True:
            if not budget.take():
                return StepResult(step, False, "Step limit reached", done, budget_exhausted=True)
            resp = self.llm.complete(messages, schemas)
            if not resp.tool_calls:
                return self._finish(step, resp.content, done)
            messages.append(assistant_message(resp))
            for call in resp.tool_calls:
                result = self.pipeline.execute(call, self.ctx)
                done.append((call, result))
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": render_result(result)}
                )
                if result.declined or result.refused:
                    return self._finish(step, render_result(result), done)

    @staticmethod
    def _finish(
        step: PlanStep, summary: str, done: list[tuple[ToolCall, ToolResult]]
    ) -> StepResult:
        declined = any(r.declined for _, r in done)
        refused = any(r.refused for _, r in done)
        ok = not declined and not refused and (not done or done[-1][1].ok)
        return StepResult(step, ok, summary, done, declined=declined, refused=refused)
