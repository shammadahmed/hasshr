"""STUB typed file tools: owned by M3 (Tools & platform).

Minimal but working so the walking skeleton runs end to end. M3 replaces/extends these
(find_files, read_file, copy_file, delete_to_trash, edit_file...) and adds journal hooks.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from termiai.contracts import Context, Reversibility, RiskLevel, ToolResult
from termiai.tools.base import Tool


def _p(value: str) -> Path:
    return Path(value).expanduser()


class ListFiles(Tool):
    name = "list_files"
    description = "List the files and folders inside a directory."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Directory path"}},
        "required": ["path"],
    }
    read_only = True

    def run(self, args: dict[str, Any], ctx: Context) -> ToolResult:
        path = _p(args.get("path", "."))
        if not path.is_dir():
            return ToolResult(ok=False, error=f"Not a directory: {path}")
        lines = []
        for i, item in enumerate(sorted(path.iterdir())):
            if i >= 200:
                lines.append("... (truncated)")
                break
            lines.append(f"{'[dir] ' if item.is_dir() else ''}{item.name}")
        return ToolResult(ok=True, output="\n".join(lines) or "(empty)")


class CreateFolder(Tool):
    name = "create_folder"
    description = "Create a folder (and any missing parent folders)."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    default_risk = RiskLevel.MODERATE

    def run(self, args: dict[str, Any], ctx: Context) -> ToolResult:
        path = _p(args["path"])
        path.mkdir(parents=True, exist_ok=True)
        return ToolResult(ok=True, output=f"Created {path}")


class MoveFile(Tool):
    name = "move_file"
    description = "Move a file or folder. If dst is an existing folder, move it inside."
    parameters = {
        "type": "object",
        "properties": {"src": {"type": "string"}, "dst": {"type": "string"}},
        "required": ["src", "dst"],
    }
    default_risk = RiskLevel.MODERATE
    default_reversibility = Reversibility.FULL

    def run(self, args: dict[str, Any], ctx: Context) -> ToolResult:
        src, dst = _p(args["src"]), _p(args["dst"])
        if not src.exists():
            return ToolResult(ok=False, error=f"Does not exist: {src}")
        target = dst / src.name if dst.is_dir() else dst
        if target.exists():
            return ToolResult(ok=False, error=f"Refusing to overwrite: {target}")
        shutil.move(str(src), str(target))
        return ToolResult(ok=True, output=f"Moved {src} -> {target}")
