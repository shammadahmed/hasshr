"""Tool registry: the single place the Executor looks tools up."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Optional

from .base import Preview, Tool, ToolContext


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool, replace: bool = False) -> Tool:
        if not tool.name:
            raise ValueError("Tool must have a name")
        if tool.name in self._tools and not replace and type(tool) is type(self._tools[tool.name]):
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __iter__(self) -> Iterable[Tool]:
        return iter(self._tools.values())

    def all(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self, read_only: bool | None = None) -> list[dict[str, Any]]:
        return [
            t.spec()
            for t in self._tools.values()
            if read_only is None or t.read_only == read_only
        ]

    def specs(self, *, read_only_only: bool = False) -> list[dict[str, Any]]:
        """Tool definitions for llm.complete(tools=...). During a Case's diagnosis
        phase pass read_only_only=True so the model is never even offered a
        state-changing tool (the Risk Engine still enforces it independently)."""
        return [t.spec() for t in self._tools.values() if t.read_only or not read_only_only]

    def preview(self, name: str, args: dict[str, Any], ctx: ToolContext) -> Preview:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
        return tool.preview(args, ctx)


def default_registry() -> ToolRegistry:
    """Registry with every M3 tool registered."""
    from .clipboard import CopyToClipboard
    from .edit import EditFile
    from .files import (
        CopyFile,
        CreateFolder,
        DeleteToTrash,
        FindFiles,
        ListFiles,
        MoveFile,
        ReadFile,
    )
    from .kernel import ListKernels, RebootIntoKernelOnce
    from .shell import RunShell
    from .system import (
        GetSystemInfo,
        ReadDeviceInfo,
        ReadLogs,
        ReadPackageState,
        ReadServiceState,
    )
    from .web import OpenUrl, SearchWeb

    reg = ToolRegistry()
    for cls in (
        ListFiles, FindFiles, ReadFile, MoveFile, CopyFile, CreateFolder, DeleteToTrash,
        EditFile, RunShell, SearchWeb, OpenUrl, CopyToClipboard, GetSystemInfo,
        ReadLogs, ReadDeviceInfo, ReadPackageState, ReadServiceState,
        ListKernels, RebootIntoKernelOnce,
    ):
        reg.register(cls())
    return reg
