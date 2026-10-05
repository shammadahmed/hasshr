"""copy_to_clipboard (F-9): copies text and prints a confirmation only.

The copied text is deliberately NOT echoed back (it may be long or sensitive,
and echoing it would put it into the LLM context and the logs).
"""

from __future__ import annotations

import subprocess
from typing import Any

from ..contracts import Reversibility, ToolResult
from .base import Tool, ToolContext, ToolError

MAX_CLIPBOARD_CHARS = 1_000_000


def _copy_with_pyperclip(text: str) -> bool:
    try:
        import pyperclip

        pyperclip.copy(text)
        return True
    except Exception:  # ImportError or PyperclipException: fall back to OS commands
        return False


def _copy_with_commands(text: str, ctx: ToolContext) -> bool:
    for copy_argv, _paste in ctx.adapter.clipboard_commands():  # type: ignore[union-attr]
        try:
            subprocess.run(copy_argv, input=text.encode("utf-8"), check=True, timeout=10,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


class CopyToClipboard(Tool):
    name = "copy_to_clipboard"
    description = "Copy text to the user's clipboard. Only a confirmation is shown, not the text."
    # Overwrites whatever was on the clipboard, which cannot be restored.
    reversibility = Reversibility.PARTIAL
    parameters = {"text": {"type": "string", "description": "The text to copy."}}
    required = ("text",)

    def explain(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return "Copy text to your clipboard so you can paste it."

    def undo_hint_for(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return "The previous clipboard content is replaced and cannot be restored."

    def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        text = str(args["text"])
        if len(text) > MAX_CLIPBOARD_CHARS:
            raise ToolError("That text is too large to copy to the clipboard.")
        if not (_copy_with_pyperclip(text) or _copy_with_commands(text, ctx)):
            raise ToolError("Could not access the clipboard on this machine (no clipboard tool found). "
                            "On Linux, install 'xclip' or 'wl-clipboard'.")
        return ToolResult(ok=True, output=f"Copied {len(text)} characters to the clipboard.",
                          data={"characters": len(text)}, reversibility=Reversibility.PARTIAL,
                          undo_hint=self.undo_hint_for(args, ctx))
