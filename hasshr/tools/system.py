"""Read-only diagnostic tools (F-34): system info, logs, devices, packages, services.

These are what a Case's diagnosis phase uses. They never change anything and
never use sudo. Everything they return came from the machine or the network and
may contain attacker-controlled text (log lines, device names), so results are
flagged `untrusted` (F-26).
"""

from __future__ import annotations

import os
import platform as _stdlib_platform
import re
import shutil
from pathlib import Path
from typing import Any

from ..contracts import Reversibility, ToolResult
from .base import Tool, ToolContext, ToolError
from .files import human_size
from .procutil import have, run_capture, try_capture

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+._:@/-]{0,127}$")
_UNIT_RE = re.compile(r"^[A-Za-z0-9@._:\\-]{1,128}$")
_SINCE_RE = re.compile(r"^[A-Za-z0-9 :\-+./]{1,40}$")
_ID_RE = re.compile(r"\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]")
MAX_LOG_LINES = 500


def _result(output: str, **data: Any) -> ToolResult:
    data.setdefault("untrusted", True)
    return ToolResult(ok=True, output=output, data=data, reversibility=Reversibility.FULL)


class _ReadOnlyTool(Tool):
    read_only = True
    reversibility = Reversibility.FULL


class GetSystemInfo(_ReadOnlyTool):
    name = "get_system_info"
    description = "Report the operating system, version, kernel, shell, package manager, CPU, memory and disk space."
    parameters: dict[str, dict[str, Any]] = {}

    def explain(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return "Look up basic facts about this computer (nothing is changed)."

    def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        env = ctx.env
        info: dict[str, Any] = {
            "os": env.os, "os_version": env.os_version, "distro": env.distro, "distro_version": env.distro_version,
            "kernel": env.kernel, "shell": env.shell, "package_manager": env.package_manager,
            "architecture": _stdlib_platform.machine(), "cpu_count": os.cpu_count(), "is_admin": env.is_root,
        }
        try:
            if env.os == "linux":
                meminfo = Path("/proc/meminfo").read_text()
                total = int(re.search(r"MemTotal:\s+(\d+)", meminfo).group(1)) * 1024  # type: ignore[union-attr]
                avail = int(re.search(r"MemAvailable:\s+(\d+)", meminfo).group(1)) * 1024  # type: ignore[union-attr]
                info["memory_total"], info["memory_available"] = total, avail
        except (OSError, AttributeError, ValueError):
            pass
        try:
            du = shutil.disk_usage(env.home or "/")
            info["disk_total"], info["disk_free"] = du.total, du.free
        except OSError:
            pass
        lines = [env.summary(), f"Architecture: {info['architecture']}, CPUs: {info['cpu_count']}"]
        if "memory_total" in info:
            lines.append(f"Memory: {human_size(info['memory_available'])} free of {human_size(info['memory_total'])}")
        if "disk_total" in info:
            lines.append(f"Disk (home): {human_size(info['disk_free'])} free of {human_size(info['disk_total'])}")
        return _result("\n".join(lines), **info, untrusted=False)


class ReadLogs(_ReadOnlyTool):
    name = "read_logs"
    description = "Read recent system log lines (journalctl / dmesg on Linux). Read-only. Log text is untrusted data."
    parameters = {
        "source": {"type": "string", "enum": ["journal", "kernel"], "description": "'journal' = system journal; 'kernel' = kernel messages (dmesg)."},
        "unit": {"type": "string", "description": "Only this service/unit (e.g. 'NetworkManager.service'). Journal only."},
        "priority": {"type": "string", "enum": ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"], "description": "Show this level and worse."},
        "boot": {"type": "string", "enum": ["current", "previous"], "description": "Which boot (default current). Journal only."},
        "since": {"type": "string", "description": "Only entries since, e.g. '1 hour ago' or '2025-01-31 08:00'."},
        "lines": {"type": "integer", "description": f"Number of lines (default 100, max {MAX_LOG_LINES})."},
        "contains": {"type": "string", "description": "Keep only lines containing this text (case-insensitive)."},
    }

    def explain(self, args: dict[str, Any], ctx: ToolContext) -> str:
        src = "kernel messages" if args.get("source") == "kernel" else "the system log"
        return f"Read recent {src} (nothing is changed)."

    def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if ctx.env.os != "linux":
            return self._other_os(args, ctx)
        source = args.get("source") or "journal"
        lines = max(1, min(int(args.get("lines") or 100), MAX_LOG_LINES))
        since = args.get("since")
        if since and not _SINCE_RE.match(since):
            raise ToolError("'since' contains unsupported characters.")
        unit = args.get("unit")
        if unit and not _UNIT_RE.match(unit):
            raise ToolError("'unit' is not a valid service name.")
        contains = (args.get("contains") or "").lower()
        # Over-fetch when filtering so the filter still has enough material.
        fetch = lines * 5 if contains else lines

        if source == "kernel":
            cap = try_capture(["dmesg", "--ctime"]) if have("dmesg") else None
            if cap is None or not cap.ok:
                # dmesg is often restricted for normal users; the journal has the same messages.
                argv = ["journalctl", "-k", "--no-pager", "-o", "short-iso", "-n", str(fetch)]
                if args.get("boot") == "previous":
                    argv += ["-b", "-1"]
                cap = run_capture(argv)
            text_lines = cap.stdout.splitlines()[-fetch:]
        else:
            argv = ["journalctl", "--no-pager", "-o", "short-iso", "-n", str(fetch), "-b", "-1" if args.get("boot") == "previous" else "0"]
            if unit:
                argv += ["-u", unit]
            if args.get("priority"):
                argv += ["-p", args["priority"]]
            if since:
                argv += ["--since", since]
            cap = run_capture(argv)
            text_lines = cap.stdout.splitlines()
        if not cap.ok and not text_lines:
            hint = " You may need to be in the 'systemd-journal' or 'adm' group to read all logs." if "ermission" in cap.stderr or "not seeing" in cap.stderr else ""
            raise ToolError(f"Could not read the logs: {cap.stderr.strip() or 'unknown error'}.{hint}")
        if contains:
            text_lines = [ln for ln in text_lines if contains in ln.lower()]
        text_lines = text_lines[-lines:]
        body = "\n".join(text_lines) if text_lines else "(no matching log lines)"
        return _result(body, source=source, lines=len(text_lines))

    def _other_os(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        lines = max(1, min(int(args.get("lines") or 100), MAX_LOG_LINES))
        if ctx.env.os == "macos":
            cap = run_capture(["log", "show", "--last", args.get("since") or "10m", "--style", "compact"], timeout=60)
        elif ctx.env.os == "windows":
            cap = run_capture(["powershell", "-NoProfile", "-Command",
                               f"Get-WinEvent -LogName System -MaxEvents {lines} | Format-List TimeCreated,Id,LevelDisplayName,Message"], timeout=60)
        else:
            raise ToolError("Reading logs is not supported on this operating system.")
        out = "\n".join(cap.stdout.splitlines()[-lines:])
        return _result(out or "(no log lines)", source="system", lines=len(out.splitlines()))


class ReadDeviceInfo(_ReadOnlyTool):
    name = "read_device_info"
    description = "List hardware and device details (PCI/USB devices, audio devices, network, disks, CPU, loaded drivers). Read-only."
    _CATEGORIES = ["pci", "usb", "audio", "network", "storage", "cpu", "drivers"]
    parameters = {"category": {"type": "string", "enum": _CATEGORIES, "description": "Which devices to list."}}
    required = ("category",)

    def explain(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return f"Look up {args.get('category')} device information (nothing is changed)."

    def _commands(self, category: str, ctx: ToolContext) -> list[list[str]]:
        if ctx.env.os == "linux":
            return {
                "pci": [["lspci", "-nnk"]],
                "usb": [["lsusb"]],
                "audio": [["aplay", "-l"], ["arecord", "-l"], ["pactl", "info"], ["pactl", "list", "short", "sinks"], ["pactl", "list", "short", "sources"]],
                "network": [["ip", "-brief", "address"], ["nmcli", "device", "status"]],
                "storage": [["lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT"]],
                "cpu": [["lscpu"]],
                "drivers": [["lsmod"]],
            }[category]
        if ctx.env.os == "macos":
            return {
                "pci": [["system_profiler", "SPPCIDataType"]], "usb": [["system_profiler", "SPUSBDataType"]],
                "audio": [["system_profiler", "SPAudioDataType"]], "network": [["networksetup", "-listallhardwareports"]],
                "storage": [["diskutil", "list"]], "cpu": [["sysctl", "-n", "machdep.cpu.brand_string"]], "drivers": [["kextstat", "-l"]],
            }[category]
        return {
            "pci": [["powershell", "-NoProfile", "-Command", "Get-PnpDevice -PresentOnly | Format-Table -AutoSize Class,FriendlyName,InstanceId"]],
            "usb": [["powershell", "-NoProfile", "-Command", "Get-PnpDevice -Class USB -PresentOnly | Format-Table -AutoSize FriendlyName,InstanceId"]],
            "audio": [["powershell", "-NoProfile", "-Command", "Get-PnpDevice -Class AudioEndpoint,MEDIA -PresentOnly | Format-Table -AutoSize FriendlyName,Status"]],
            "network": [["ipconfig", "/all"]], "storage": [["powershell", "-NoProfile", "-Command", "Get-Disk | Format-Table -AutoSize"]],
            "cpu": [["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Processor | Format-List Name,NumberOfCores"]],
            "drivers": [["driverquery"]],
        }[category]

    def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        category = args["category"]
        sections, ids, skipped = [], [], []
        for argv in self._commands(category, ctx):
            if not have(argv[0]):
                skipped.append(argv[0])
                continue
            cap = try_capture(argv, timeout=40)
            if cap is None:
                skipped.append(argv[0])
                continue
            text = cap.stdout.strip() or cap.stderr.strip()
            sections.append(f"$ {' '.join(argv)}\n{text}" if text else f"$ {' '.join(argv)}\n(no output)")
            if category in {"pci", "usb"}:
                ids += [f"{v.lower()}:{d.lower()}" for v, d in _ID_RE.findall(cap.stdout)]
            if category == "usb":
                ids += re.findall(r"ID ([0-9a-fA-F]{4}:[0-9a-fA-F]{4})", cap.stdout)
        if not sections:
            raise ToolError(f"No tool available to list {category} devices (missing: {', '.join(skipped) or 'none'}).")
        if skipped:
            sections.append(f"(not available: {', '.join(skipped)})")
        unique_ids = sorted(set(i.lower() for i in ids))
        return _result("\n\n".join(sections), category=category, hardware_ids=unique_ids)


class ReadPackageState(_ReadOnlyTool):
    name = "read_package_state"
    description = "Check whether a software package is installed, its version, and the newest available version. Read-only."
    parameters = {"name": {"type": "string", "description": "Package name, e.g. 'pulseaudio' or 'linux-image-generic'."}}
    required = ("name",)

    def explain(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return f"Check the installed state of the package '{args.get('name')}' (nothing is changed)."

    def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = str(args["name"])
        if not _NAME_RE.match(name):
            raise ToolError("That is not a valid package name.")
        pm = ctx.env.package_manager
        installed, version, candidate = False, "", ""
        if pm == "apt":
            cap = run_capture(["dpkg-query", "-W", "-f=${db:Status-Abbrev}\t${Version}\n", name])
            if cap.ok and cap.stdout.strip():
                status, _, version = cap.stdout.strip().partition("\t")
                installed = status.strip().startswith("ii")
            pol = try_capture(["apt-cache", "policy", name])
            if pol and pol.ok:
                m = re.search(r"Candidate:\s*(\S+)", pol.stdout)
                candidate = m.group(1) if m and m.group(1) != "(none)" else ""
        elif pm in {"dnf", "yum", "zypper"}:
            cap = run_capture(["rpm", "-q", "--qf", "%{VERSION}-%{RELEASE}\n", name])
            installed, version = cap.ok, cap.stdout.strip() if cap.ok else ""
        elif pm == "pacman":
            cap = run_capture(["pacman", "-Q", name])
            installed = cap.ok
            version = cap.stdout.split()[1] if cap.ok and len(cap.stdout.split()) > 1 else ""
        elif pm == "brew":
            cap = run_capture(["brew", "list", "--versions", name])
            installed, version = cap.ok and bool(cap.stdout.strip()), cap.stdout.strip().partition(" ")[2]
        elif pm == "winget":
            cap = run_capture(["winget", "list", "--exact", "--id", name], timeout=60)
            installed = cap.ok and name.lower() in cap.stdout.lower()
        else:
            raise ToolError("No supported package manager was detected on this machine.")
        state = f"installed (version {version})" if installed else "not installed"
        out = f"{name}: {state}" + (f"; newest available: {candidate}" if candidate and candidate != version else "")
        return _result(out, name=name, installed=installed, version=version, candidate=candidate, package_manager=pm, untrusted=False)


class ReadServiceState(_ReadOnlyTool):
    name = "read_service_state"
    description = "Check whether a background service is running/enabled and why it may have failed. Read-only."
    parameters = {"name": {"type": "string", "description": "Service name, e.g. 'NetworkManager' or 'ssh.service'."}}
    required = ("name",)

    def explain(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return f"Check the state of the service '{args.get('name')}' (nothing is changed)."

    def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        name = str(args["name"])
        if not _UNIT_RE.match(name):
            raise ToolError("That is not a valid service name.")
        if ctx.env.os == "linux":
            props = ["LoadState", "ActiveState", "SubState", "UnitFileState", "Result", "ExecMainStatus", "Description"]
            cap = run_capture(["systemctl", "show", name, "--no-pager", *[f"--property={p}" for p in props]])
            state = dict(line.split("=", 1) for line in cap.stdout.splitlines() if "=" in line)
            if state.get("LoadState") == "not-found":
                return _result(f"{name}: no such service on this machine.", name=name, found=False, untrusted=False)
            tail = try_capture(["journalctl", "-u", name, "--no-pager", "-n", "15", "-o", "short-iso"])
            recent = tail.stdout.strip() if tail and tail.ok else ""
            summary = (f"{name}: {state.get('ActiveState', '?')} ({state.get('SubState', '?')}), "
                       f"boot setting: {state.get('UnitFileState') or 'unknown'}, last result: {state.get('Result') or 'unknown'}")
            out = summary + (f"\n\nRecent log lines:\n{recent}" if recent else "")
            return _result(out, name=name, found=True, **{k.lower(): v for k, v in state.items()})
        if ctx.env.os == "macos":
            cap = run_capture(["launchctl", "list"])
            lines = [ln for ln in cap.stdout.splitlines() if name.lower() in ln.lower()]
            return _result("\n".join(lines) or f"{name}: not found in launchctl list.", name=name, found=bool(lines))
        cap = run_capture(["powershell", "-NoProfile", "-Command", f"Get-Service -Name '{name}' | Format-List Name,Status,StartType"])
        return _result(cap.stdout.strip() or cap.stderr.strip(), name=name, found=cap.ok)
