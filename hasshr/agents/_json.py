from __future__ import annotations

import json
from typing import Any


def extract_json(text: str) -> dict[str, Any] | None:
    """Parse the first {...} object in an LLM reply (tolerates prose or code fences)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
