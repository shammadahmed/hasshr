from __future__ import annotations

from termiai.agents._json import extract_json
from termiai.contracts import Context, LLMClient, Plan, PlanStep
from termiai.prompts import planner_system


class Planner:
    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    def plan(
        self,
        goal: str,
        ctx: Context,
        feedback: str | None = None,
        previous: Plan | None = None,
    ) -> Plan:
        user = f"User request: {goal}"
        if previous is not None and feedback:
            done = "\n".join(f"- {s.description}" for s in previous.steps)
            user += (
                f"\n\nA previous plan did not achieve the goal.\nPrevious steps:\n{done}\n"
                f"Verifier feedback: {feedback}\nWrite a corrected plan."
            )
        messages = [
            {"role": "system", "content": planner_system(ctx)},
            {"role": "user", "content": user},
        ]
        text = self.llm.complete(messages, None).content
        data = extract_json(text) or {}
        steps = [
            PlanStep(str(s["description"]))
            for s in data.get("steps", [])
            if isinstance(s, dict) and s.get("description")
        ]
        if not steps:  # never fail on a badly formatted plan
            steps = [PlanStep(goal)]
        return Plan(goal=goal, steps=steps)
