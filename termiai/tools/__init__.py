from termiai.tools.base import Tool, ToolRegistry
from termiai.tools.files import CreateFolder, ListFiles, MoveFile
from termiai.tools.shell import RunShell


def default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (ListFiles(), CreateFolder(), MoveFile(), RunShell()):
        registry.register(tool)
    return registry


__all__ = ["Tool", "ToolRegistry", "default_registry"]
