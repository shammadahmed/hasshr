"""Snapshot data collectors for M4 (`snapshot.take(scope)`).

Pure functions that return plain, JSON-serialisable dicts describing the
machine's state: installed packages, service states, kernel module
parameters, tracked file hashes, and kernel facts. M4 stores them in a
`Snapshot` and diffs two of them to verify a rollback (F-46).

All collectors are read-only, never use sudo, and never raise: if a source is
unavailable they return an empty result (and `collect_all` notes what was
skipped under "unavailable") so a missing tool cannot break a Case.
"""

from __future__ import annotations

import glob
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from ..platform import EnvInfo
from .files import sha256_file
from .procutil import try_capture

#: Files and folders worth tracking in a Case (config that fixes usually touch).
DEFAULT_TRACKED_FILES = [
    "/etc/modprobe.d/*", "/etc/modules-load.d/*", "/etc/modules", "/etc/default/grub", "/etc/fstab",
    "/etc/environment", "/etc/pulse/*", "/etc/alsa/*", "/etc/asound.conf", "/etc/pipewire/*",
    "/etc/NetworkManager/NetworkManager.conf", "/etc/NetworkManager/conf.d/*",
    "/etc/systemd/system/*.service", "/etc/systemd/system/*.conf", "/etc/systemd/resolved.conf",
    "/etc/hosts", "/etc/resolv.conf", "/etc/sysctl.conf", "/etc/sysctl.d/*", "/etc/udev/rules.d/*",
]
MAX_PARAM_VALUE = 200


def collect_packages(env: EnvInfo) -> dict[str, str]:
    """name -> version for all installed packages ({} if unsupported)."""
    pm = env.package_manager
    if pm == "apt":
        cap = try_capture(["dpkg-query", "-W", "-f=${Package}\t${Version}\t${db:Status-Abbrev}\n"], timeout=60)
        out: dict[str, str] = {}
        if cap and cap.ok:
            for line in cap.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) == 3 and parts[2].strip().startswith("ii"):
                    out[parts[0]] = parts[1]
        return out
    if pm in {"dnf", "yum", "zypper"}:
        cap = try_capture(["rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\n"], timeout=60)
        return _tab_pairs(cap.stdout) if cap and cap.ok else {}
    if pm == "pacman":
        cap = try_capture(["pacman", "-Q"], timeout=60)
        return {p[0]: p[1] for p in (ln.split() for ln in cap.stdout.splitlines()) if len(p) >= 2} if cap and cap.ok else {}
    if pm == "brew":
        cap = try_capture(["brew", "list", "--versions"], timeout=120)
        return {p[0]: " ".join(p[1:]) for p in (ln.split() for ln in cap.stdout.splitlines()) if len(p) >= 2} if cap and cap.ok else {}
    return {}


def _tab_pairs(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        name, _, ver = line.partition("\t")
        if name:
            out[name] = ver
    return out


def collect_services(env: EnvInfo) -> dict[str, dict[str, str]]:
    """service -> {"enabled": ..., "active": ...} (systemd on Linux; {} elsewhere)."""
    if env.os != "linux":
        return {}
    files = try_capture(["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"], timeout=60)
    units = try_capture(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager", "--plain"], timeout=60)
    result: dict[str, dict[str, str]] = {}
    if files and files.ok:
        for line in files.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].endswith(".service"):
                result[parts[0]] = {"enabled": parts[1], "active": "unknown"}
    if units and units.ok:
        for line in units.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0].endswith(".service"):
                result.setdefault(parts[0], {"enabled": "unknown", "active": "unknown"})["active"] = f"{parts[2]}/{parts[3]}"
    return result


def collect_module_params(modules: Optional[Iterable[str]] = None, base: Path = Path("/sys/module")) -> dict[str, dict[str, str]]:
    """module -> {parameter: value} read from /sys/module (Linux only).

    With modules=None, every loaded module that exposes parameters is included.
    `base` exists so tests can point at a fake sysfs tree.
    """
    if not base.is_dir():
        return {}
    names = list(modules) if modules is not None else sorted(p.name for p in base.iterdir())
    out: dict[str, dict[str, str]] = {}
    for mod in names:
        pdir = base / mod / "parameters"
        if not pdir.is_dir():
            continue
        params: dict[str, str] = {}
        try:
            for p in sorted(pdir.iterdir()):
                try:
                    params[p.name] = p.read_text(errors="replace").strip()[:MAX_PARAM_VALUE]
                except OSError:
                    continue  # write-only or restricted parameter
        except OSError:
            continue
        if params:
            out[mod] = params
    return out


def collect_file_hashes(patterns: Optional[Iterable[str]] = None) -> dict[str, Optional[str]]:
    """path -> sha256 (None if unreadable) for files matching the glob patterns.

    A pattern that matches nothing yields no entry; M4 treats a path that
    appears or disappears between snapshots as created/removed.
    """
    out: dict[str, Optional[str]] = {}
    for pattern in (patterns if patterns is not None else DEFAULT_TRACKED_FILES):
        for match in sorted(glob.glob(os.path.expanduser(pattern))):
            p = Path(match)
            if p.is_file():
                out[str(p)] = sha256_file(p)
    return out


def collect_kernel() -> dict[str, object]:
    """Running kernel release, boot command line and loaded module names."""
    import platform as _p

    info: dict[str, object] = {"release": _p.release()}
    try:
        info["cmdline"] = Path("/proc/cmdline").read_text().strip()
    except OSError:
        pass
    try:
        info["modules_loaded"] = sorted(line.split()[0] for line in Path("/proc/modules").read_text().splitlines() if line.strip())
    except OSError:
        pass
    return info


def collect_all(env: EnvInfo, tracked_files: Optional[Iterable[str]] = None, modules: Optional[Iterable[str]] = None) -> dict[str, object]:
    """Everything a Snapshot needs, in one call."""
    packages = collect_packages(env)
    services = collect_services(env)
    unavailable = []
    if not packages:
        unavailable.append("packages")
    if not services:
        unavailable.append("services")
    return {
        "packages": packages,
        "services": services,
        "module_params": collect_module_params(modules) if env.os == "linux" else {},
        "file_hashes": collect_file_hashes(tracked_files) if env.os != "windows" else {},
        "kernel": collect_kernel() if env.os == "linux" else {"release": env.kernel},
        "unavailable": unavailable,
    }
