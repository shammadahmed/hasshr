"""LLM provider layer: owned by M6 (CLI/UX, LLM layer & packaging).

Provides llm.complete(messages, tools, model) using LiteLLM (Anthropic, OpenAI,
Google, Ollama/LM Studio) with a fallback provider when LiteLLM is not installed.
"""

from __future__ import annotations

import json
import os
from typing import Any

from hasshr.config import load_config
from hasshr.contracts import LLMClient, LLMResponse, ToolCall


class LiteLLMClient(LLMClient):
    """LiteLLM-backed provider supporting cloud and local models."""

    def __init__(self, model: str | None = None) -> None:
        cfg = load_config()
        self.model = model or os.environ.get("HASSHR_MODEL") or cfg.get("model", "gpt-4o")
        self.api_base = cfg.get("api_base")

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse:
        try:
            import litellm
        except ImportError as err:
            raise NotImplementedError(
                "LiteLLM is not installed. Install it with `pip install litellm` "
                "or run with `--mock` to use the scripted demo engine."
            ) from err

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if self.api_base:
            kwargs["api_base"] = self.api_base

        response = litellm.completion(**kwargs)
        message = response.choices[0].message
        content = message.content or ""
        tool_calls: list[ToolCall] = []
        if getattr(message, "tool_calls", None):
            for tc in message.tool_calls:
                func = tc.function
                args = json.loads(func.arguments) if isinstance(func.arguments, str) else func.arguments
                tool_calls.append(ToolCall(name=func.name, args=args, id=tc.id))
        return LLMResponse(content=content, tool_calls=tool_calls)


def get_client(model: str | None = None) -> LLMClient:
    """Return an LLMClient instance for the requested or configured model."""
    return LiteLLMClient(model=model)
