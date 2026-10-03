"""Scripted LLM for tests and the `--mock` walking skeleton. No network, fully deterministic."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from termiai.contracts import LLMResponse, ToolCall

Responder = Callable[[list[dict[str, Any]], list[dict[str, Any]] | None], LLMResponse]


def reply(content: str = "", *calls: ToolCall) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=list(calls))


class ScriptedLLM:
    """Returns queued responses in order. Items may be LLMResponse or a callable."""

    def __init__(self, responses: list[LLMResponse | Responder]) -> None:
        self._queue = list(responses)
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        self.calls.append((list(messages), tools))
        if not self._queue:
            raise RuntimeError("ScriptedLLM ran out of responses")
        item = self._queue.pop(0)
        return item(messages, tools) if callable(item) else item


def demo_script(path: str = "~/Downloads") -> ScriptedLLM:
    """Canned conversation for `termiai --mock`: plan -> list a folder -> summary -> verify."""
    return ScriptedLLM(
        [
            reply('{"steps": [{"description": "Look at what is inside the folder"}]}'),
            reply(
                "", ToolCall("list_files", {"path": path}, explanation="Inspect the folder first")
            ),
            reply("Listed the folder."),
            reply('{"ok": true, "notes": "The folder was listed successfully."}'),
        ]
    )
