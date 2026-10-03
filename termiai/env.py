"""Environment detection (PRD F-10). Minimal version by M1; M3 extends (macOS/Windows polish)."""

from __future__ import annotations

import os
import platform
import shutil

from termiai.contracts import EnvInfo


def _distro() -> str:
    try:
        with open("/etc/os-release", encoding="utf-8") as f:
            data = dict(line.strip().split("=", 1) for line in f if "=" in line)
        return data.get("PRETTY_NAME", "linux").strip('"')
    except OSError:
        return platform.platform()


def detect_env() -> EnvInfo:
    system = platform.system().lower()
    distro = _distro() if system == "linux" else platform.platform()
    pm = next(
        (
            m
            for m in ("apt", "dnf", "pacman", "zypper", "brew", "winget", "choco")
            if shutil.which(m)
        ),
        "unknown",
    )
    shell = os.environ.get("SHELL") or os.environ.get("COMSPEC") or "unknown"
    return EnvInfo(
        os_name=system,
        distro=distro,
        shell=shell,
        package_manager=pm,
        home=os.path.expanduser("~"),
    )
