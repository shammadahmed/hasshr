"""Platform detection and OS adapters (M3)."""

from .adapters import LinuxAdapter, MacAdapter, OSAdapter, WindowsAdapter, get_adapter
from .detect import EnvInfo, detect_environment

__all__ = [
    "EnvInfo",
    "detect_environment",
    "OSAdapter",
    "LinuxAdapter",
    "MacAdapter",
    "WindowsAdapter",
    "get_adapter",
]

