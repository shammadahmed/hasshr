"""Environment detection (F-10): OS, distro/version, shell, package manager."""

from __future__ import annotations

import os
import platform as _stdlib_platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Package managers in preference order per OS family.
_LINUX_PMS = ["apt", "dnf", "yum", "pacman", "zypper", "apk"]
_MAC_PMS = ["brew", "port"]
_WIN_PMS = ["winget", "choco", "scoop"]


@dataclass
class EnvInfo:
    """Facts about the machine, injected into the agent's system prompt."""

    os: str = "unknown"  # "linux" | "macos" | "windows"
    os_name: str = ""
    os_version: str = ""
    distro: str = ""
    distro_version: str = ""
    kernel: str = ""
    shell: str = ""
    package_manager: Optional[str] = None
    is_root: bool = False
    home: str = ""
    extras: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.os and (not self.os_name or self.os_name == "unknown"):
            self.os_name = self.os
        elif self.os_name and (not self.os or self.os == "unknown"):
            self.os = self.os_name

    def summary(self) -> str:
        """One-line description suitable for a system prompt."""
        parts = [f"OS: {self.os_name or self.os}"]
        if self.distro:
            parts.append(f"distro: {self.distro} {self.distro_version}".strip())
        elif self.os_version:
            parts.append(f"version: {self.os_version}")
        if self.kernel:
            parts.append(f"kernel: {self.kernel}")
        parts.append(f"shell: {self.shell or 'unknown'}")
        parts.append(f"package manager: {self.package_manager or 'none detected'}")
        return "; ".join(parts)


def detect_os() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform in ("win32", "cygwin"):
        return "windows"
    return sys.platform


def parse_os_release(text: str) -> dict[str, str]:
    """Parse the contents of /etc/os-release into a dict."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if not key.strip():
            continue
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _read_os_release() -> dict[str, str]:
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            return parse_os_release(Path(path).read_text())
        except OSError:
            continue
    return {}


def detect_shell(os_name: str) -> str:
    if os_name == "windows":
        # PowerShell sets PSModulePath in every session; COMSPEC points at cmd.
        if os.environ.get("PSModulePath") and not os.environ.get("PROMPT"):
            return "powershell"
        comspec = os.environ.get("COMSPEC", "")
        return Path(comspec).name.lower().removesuffix(".exe") if comspec else "cmd"
    shell = os.environ.get("SHELL", "")
    return Path(shell).name if shell else "sh"


def detect_package_manager(os_name: str) -> Optional[str]:
    candidates = {"linux": _LINUX_PMS, "macos": _MAC_PMS, "windows": _WIN_PMS}.get(os_name, [])
    for pm in candidates:
        if shutil.which(pm):
            return pm
    return None


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def detect_environment() -> EnvInfo:
    """Detect the current machine. Never raises; missing facts stay empty."""
    os_name = detect_os()
    info = EnvInfo(
        os=os_name,
        os_name=os_name,
        kernel=_stdlib_platform.release(),
        shell=detect_shell(os_name),
        package_manager=detect_package_manager(os_name),
        is_root=_is_root(),
        home=str(Path.home()),
    )
    if os_name == "linux":
        rel = _read_os_release()
        info.distro = rel.get("NAME", rel.get("ID", ""))
        info.distro_version = rel.get("VERSION_ID", "")
        info.os_version = rel.get("PRETTY_NAME", "")
    return info
