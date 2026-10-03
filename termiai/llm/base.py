"""LLM provider layer: owned by M6 (CLI/UX, LLM layer & packaging).

M6 TODO: implement get_client() with LiteLLM (Anthropic, OpenAI, Google, Ollama/LM Studio).
The agent only depends on the LLMClient protocol in contracts.py.
"""

from __future__ import annotations

from termiai.contracts import LLMClient


def get_client(model: str | None = None) -> LLMClient:
    raise NotImplementedError(
        "No LLM provider is wired up yet (M6 task). Use `termiai --mock` for the walking skeleton."
    )
