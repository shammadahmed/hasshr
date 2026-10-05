"""OS adapters: the few places where behaviour differs per operating system.

Typed tools call these instead of branching on sys.platform themselves, so the
same prompt works on Linux, macOS and Windows (PRD section 10, Portability).
"""

from __future__ import annotations

import os
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from .detect import EnvInfo, detect_environment


class OSAdapter(ABC):
    """Per-OS behaviour used by the tools."""

    name: str = "generic"

    def __init__(self, env: EnvInfo) -> None:
        self.env = env

    @abstractmethod
    def shell_argv(self, command: str) -> list[str]:
        """argv that runs `command` in the user's native shell."""

    @abstractmethod
    def elevate_prefix(self) -> list[str]:
        """argv prefix that gets admin rights (empty if already elevated/unknown)."""

    @abstractmethod
    def default_downloads(self) -> Path:
        """Default Downloads folder."""

    @abstractmethod
    def open_command(self, target: str) -> list[str]:
        """argv that opens a path/URL with the default application."""

    @abstractmethod
    def clipboard_commands(self) -> list[tuple[list[str], list[str]]]:
        """Fallback (copy_argv, paste_argv) pairs when pyperclip is unavailable."""

    def is_system_path(self, path: Path) -> bool:
        """True if writing here normally needs elevated rights."""
        return False

    def home(self) -> Path:
        return Path(self.env.home or Path.home())


class LinuxAdapter(OSAdapter):
    name = "linux"

    def shell_argv(self, command: str) -> list[str]:
        shell = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
        return [shell, "-c", command]

    def elevate_prefix(self) -> list[str]:
        return [] if self.env.is_root else ["sudo"]

    def default_downloads(self) -> Path:
        xdg = os.environ.get("XDG_DOWNLOAD_DIR")
        if xdg:
            return Path(os.path.expandvars(xdg)).expanduser()
        return self.home() / "Downloads"

    def open_command(self, target: str) -> list[str]:
        return ["xdg-open", target]

    def clipboard_commands(self) -> list[tuple[list[str], list[str]]]:
        return [
            (["wl-copy"], ["wl-paste"]),
            (["xclip", "-selection", "clipboard"], ["xclip", "-selection", "clipboard", "-o"]),
            (["xsel", "--clipboard", "--input"], ["xsel", "--clipboard", "--output"]),
        ]

    def is_system_path(self, path: Path) -> bool:
        p = str(path.resolve())
        return any(p == r or p.startswith(r + "/") for r in ("/etc", "/usr", "/boot", "/var", "/opt", "/lib", "/bin", "/sbin", "/root"))


class MacAdapter(OSAdapter):
    name = "macos"

    def shell_argv(self, command: str) -> list[str]:
        return [shutil.which("zsh") or "/bin/sh", "-c", command]

    def elevate_prefix(self) -> list[str]:
        return [] if self.env.is_root else ["sudo"]

    def default_downloads(self) -> Path:
        return self.home() / "Downloads"

    def open_command(self, target: str) -> list[str]:
        return ["open", target]

    def clipboard_commands(self) -> list[tuple[list[str], list[str]]]:
        return [(["pbcopy"], ["pbpaste"])]

    def is_system_path(self, path: Path) -> bool:
        p = str(path.resolve())
        return any(p == r or p.startswith(r + "/") for r in ("/System", "/Library", "/usr", "/etc", "/private/etc", "/var", "/sbin", "/bin"))


class WindowsAdapter(OSAdapter):
    name = "windows"

    def shell_argv(self, command: str) -> list[str]:
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if ps and self.env.shell in ("powershell", "pwsh"):
            return [ps, "-NoProfile", "-NonInteractive", "-Command", command]
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", command]

    def elevate_prefix(self) -> list[str]:
        # Windows has no sudo; elevation is a UAC prompt handled by the OS.
        return []

    def default_downloads(self) -> Path:
        return self.home() / "Downloads"

    def open_command(self, target: str) -> list[str]:
        # `start` is a cmd builtin; the empty string is the window-title argument.
        return ["cmd", "/c", "start", "", target]

    def clipboard_commands(self) -> list[tuple[list[str], list[str]]]:
        return [(["clip"], ["powershell", "-NoProfile", "-Command", "Get-Clipboard"])]

    def is_system_path(self, path: Path) -> bool:
        p = str(path).lower()
        roots = [os.environ.get("SystemRoot", r"C:\Windows"), os.environ.get("ProgramFiles", r"C:\Program Files")]
        return any(p.startswith(r.lower()) for r in roots)


def get_adapter(env: EnvInfo | None = None) -> OSAdapter:
    """Return the adapter for the detected (or given) environment."""
    env = env or detect_environment()
    cls = {"linux": LinuxAdapter, "macos": MacAdapter, "windows": WindowsAdapter}.get(env.os, LinuxAdapter)
    return cls(env)

