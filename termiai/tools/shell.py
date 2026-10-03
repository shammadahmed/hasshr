"""STUB run_shell: owned by M3 (Tools & platform); risk labels refined by M2/M4.

TODO(M3): sudo prompts must reach the user's TTY directly, never the LLM or logs.
"""

from __future__ import annotations

import dataclasses
import shlex
import subprocess
from typing import Any

from termiai.contracts import Context, Reversibility, RiskLevel, ToolCall, ToolResult
from termiai.tools.base import Tool

IRREVERSIBLE = {"rm", "dd", "mkfs", "shred", "wipefs", "fdisk", "parted"}


class RunShell(Tool):
    name = "run_shell"
    description = (
        "Run a shell command for anything the typed tools cannot do. "
        "Always include a plain-language explanation."
    )
    parameters = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }
    shell_like = True
    default_reversibility = Reversibility.PARTIAL
    timeout = 120

    def describe(self, args: dict[str, Any]) -> str:
        return str(args.get("command", ""))

    def base_risk(self, args: dict[str, Any], ctx: Context) -> RiskLevel:
        return RiskLevel.SAFE  # the classifier decides

    def reversibility_for(self, args: dict[str, Any]) -> Reversibility:
        try:
            first = shlex.split(str(args.get("command", "")))
        except ValueError:
            return self.default_reversibility
        if first and first[0] == "sudo" and len(first) > 1:
            first = first[1:]
        if first and first[0] in IRREVERSIBLE:
            return Reversibility.NONE
        return self.default_reversibility

    def with_command(self, call: ToolCall, command: str) -> ToolCall | None:
        return dataclasses.replace(call, args={**call.args, "command": command})

    def run(self, args: dict[str, Any], ctx: Context) -> ToolResult:
        command = str(args.get("command", ""))
        try:
            proc = subprocess.run(  # noqa: S602 - gated by the pipeline
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(ok=False, error=f"Timed out after {self.timeout}s", exit_code=124)
        out = (proc.stdout or "") + (proc.stderr or "")
        return ToolResult(ok=proc.returncode == 0, output=out, exit_code=proc.returncode)
