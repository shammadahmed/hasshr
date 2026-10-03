"""Tool base class and registry.

The base class and registry are the contract between M1 (pipeline) and M3 (tools).
Individual tools are owned by M3.

Per-tool declarations the pipeline relies on:
    read_only          -> exposed to the Verifier and during Case diagnosis
    shell_like         -> command string is passed to safety.classify()
    base_risk(...)     -> minimum risk for typed tools (may depend on args, e.g. /etc paths)
    reversibility_for  -> Full / Partial / None, shown to the user BEFORE the step runs
    with_command(...)  -> lets the user [e]dit a command (shell-like tools only)
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

from termiai.contracts import Context, Reversibility, RiskLevel, ToolCall, ToolResult


class Tool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    read_only: bool = False
    shell_like: bool = False
    default_risk: RiskLevel = RiskLevel.MODERATE
    default_reversibility: Reversibility = Reversibility.FULL

    def describe(self, args: dict[str, Any]) -> str:
        """Exact, human-readable description of what will run (shown on approval)."""
        return f"{self.name} {json.dumps(args, sort_keys=True)}"

    def base_risk(self, args: dict[str, Any], ctx: Context) -> RiskLevel:
        return RiskLevel.SAFE if self.read_only else self.default_risk

    def reversibility_for(self, args: dict[str, Any]) -> Reversibility:
        return self.default_reversibility

    def with_command(self, call: ToolCall, command: str) -> ToolCall | None:
        return None

    @abstractmethod
    def run(self, args: dict[str, Any], ctx: Context) -> ToolResult: ...

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self, read_only: bool | None = None) -> list[dict[str, Any]]:
        return [
            t.schema()
            for t in self._tools.values()
            if read_only is None or t.read_only == read_only
        ]
