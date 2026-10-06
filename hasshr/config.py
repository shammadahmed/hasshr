"""Configuration management for Hasshr (M6).

Loads/saves configuration from ~/.hasshr/config.json.
Supported keys:
  - provider: "openai" | "anthropic" | "gemini" | "ollama"
  - model: model identifier string
  - api_key: optional API key (prefers environment variables)
  - api_base: optional custom base URL (e.g. for Ollama / LM Studio)
  - theme: "dark" | "light"
  - mode: "ask-sensitive" | "ask-all" | "auto"
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "provider": "openai",
    "model": "gpt-4o",
    "theme": "dark",
    "mode": "ask-sensitive",
    "api_base": None,
}

CONFIG_DIR = Path.home() / ".hasshr"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path).expanduser() if path else CONFIG_FILE
    if target.exists():
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            config = dict(DEFAULT_CONFIG)
            config.update(data)
            return config
        except (OSError, json.JSONDecodeError):
            pass
    return dict(DEFAULT_CONFIG)


def save_config(config: dict[str, Any], path: str | Path | None = None) -> None:
    target = Path(path).expanduser() if path else CONFIG_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def get_api_key(provider: str = "openai") -> str | None:
    env_keys = {
        "openai": ["OPENAI_API_KEY"],
        "anthropic": ["ANTHROPIC_API_KEY"],
        "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "google": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    }
    for var in env_keys.get(provider.lower(), []):
        if os.environ.get(var):
            return os.environ[var]
    cfg = load_config()
    return cfg.get("api_key")
