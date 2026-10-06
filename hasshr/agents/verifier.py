from __future__ import annotations

from hasshr.agents._json import extract_json
from hasshr.agents.executor import StepBudget, render_result
from hasshr.contracts import (
    Context,
    LLMClient,
    Plan,
    StepResult,
    ToolResult,
    Verdict,
    assistant_message,
)
from hasshr.pipeline import Pipeline
from hasshr.prompts import verifier_system
from hasshr.tools.base import ToolRegistry

MAX_VERIFY_ROUNDS = 6


class Verifier:
    """Checks the real system state with READ-ONLY tools only (PRD F-25)."""

    def __init__(
        self,
        llm: LLMClient,
        pipeline: Pipeline,
        registry: ToolRegistry,
        ctx: Context,
    ) -> None:
        self.llm, self.pipeline, self.registry, self.ctx = llm, pipeline, registry, ctx

    def verify(
        self, goal: str, plan: Plan, results: list[StepResult], budget: StepBudget
    ) -> Verdict:
        report = "\n".join(
            f"- {r.step.description}: {'ok' if r.ok else 'FAILED'} - {r.summary}" for r in results
        )
        messages = [
            {"role": "system", "content": verifier_system(self.ctx)},
            {
                "role": "user",
                "content": f"Goal: {goal}\nWhat the Executor reports:\n{report}\n"
                "Verify the real state now.",
            },
        ]
        schemas = self.registry.schemas(read_only=True)
        for _ in range(MAX_VERIFY_ROUNDS):
            if not budget.take():
                return Verdict(False, "Step limit reached during verification", parseable=False)
            resp = self.llm.complete(messages, schemas)
            if not resp.tool_calls:
                data = extract_json(resp.content)
                if data is None or "ok" not in data:
                    return Verdict(False, "Verifier gave no usable verdict", parseable=False)
                return Verdict(bool(data["ok"]), str(data.get("notes", "")))
            messages.append(assistant_message(resp))
            for call in resp.tool_calls:
                tool = self.registry.get(call.name)
                if tool is None or not tool.read_only:
                    result = ToolResult(ok=False, error="The Verifier may only use read-only tools")
                else:
                    result = self.pipeline.execute(call, self.ctx)
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": render_result(result)}
                )
        return Verdict(False, "Verifier did not reach a verdict", parseable=False)
