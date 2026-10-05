"""Conservative command risk classifier for TermiAI (Member 2).

Public interface must remain compatible with contracts.py:
    classify(command: str, ctx: Context) -> RiskResult

The policy is intentionally fail-closed: malformed or unknown commands are SENSITIVE.
"""
from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from termiai.contracts import Context, RiskLevel, RiskResult

_RULES_PATH = Path(__file__).with_name("rules.yaml")

_DEFAULT_RULES: dict[str, Any] = {
    "safe_commands": [
        "ls", "cat", "pwd", "df", "du", "ps", "uname", "whoami", "date", "head", "tail",
        "lsblk", "free", "uptime", "aplay", "arecord", "pactl", "lspci", "lsusb", "dmesg",
        "journalctl", "lsmod", "id", "hostname", "stat", "file", "which", "grep", "find",
        "less", "more", "wc", "sort", "uniq", "cut", "tr", "printf", "echo", "env", "printenv",
        "ip", "ss", "ping", "getent", "lsusb", "lscpu", "lsmem", "mountpoint", "true", "false",
    ],
    "moderate_commands": ["mkdir", "touch", "cp", "mv", "ln", "tee", "git"],
    "sensitive_commands": [
        "sudo", "su", "doas", "apt", "apt-get", "dpkg", "dnf", "yum", "rpm", "brew",
        "winget", "choco", "systemctl", "service", "mount", "umount", "modprobe", "insmod",
        "rmmod", "iptables", "nft", "ufw", "firewall-cmd", "useradd", "usermod", "userdel",
        "passwd", "chown", "chmod", "kill", "killall", "pkill", "reboot", "shutdown", "poweroff",
        "grub-install", "update-grub", "mkinitcpio", "pacman", "zypper", "snap", "flatpak",
        "curl", "wget", "powershell", "pwsh", "cmd", "reg", "diskpart", "bcdedit", "sc.exe",
        "format", "takeown", "icacls", "net", "netsh", "Set-ExecutionPolicy",
    ],
    "blocked_patterns": [
        r"(?i)(?:^|[;&|]\s*)rm\s+(?:-[a-zA-Z]*\s+)*-[a-zA-Z]*r[a-zA-Z]*f[a-zA-Z]*\s+/(?:\s|$)",
        r"(?i)(?:^|[;&|]\s*)rm\s+(?:-[a-zA-Z]*\s+)*-[a-zA-Z]*f[a-zA-Z]*r[a-zA-Z]*\s+/(?:\s|$)",
        r"(?i)\bdd\b.*\bof=/dev/(?:sd[a-z]|nvme\d+n\d+|hd[a-z]|vd[a-z]|mmcblk\d+)",
        r"(?i)\bmkfs(?:\.\w+)?\b.*(?:/dev/|\s)",
        r":\s*\(\s*\)\s*\{.*\}\s*;\s*:",
        r"(?i)\b(shutdown|poweroff|reboot)\b.*--force.*(?:now|0)?$",
    ],
    "sensitive_paths": ["/etc/", "/boot/", "/usr/", "/bin/", "/sbin/", "/root/", "C:\\\\Windows\\\\", "C:\\\\Program Files\\\\"],
    "network_commands": ["curl", "wget", "ssh", "scp", "sftp", "nc", "netcat", "Invoke-WebRequest", "Invoke-RestMethod"],
}


def _load_rules() -> dict[str, Any]:
    """Load JSON-compatible YAML with stdlib only; fall back safely on errors."""
    try:
        raw = _RULES_PATH.read_text(encoding="utf-8")
        loaded = json.loads(raw)  # JSON is a valid subset of YAML.
        if not isinstance(loaded, dict):
            raise ValueError("rules root must be a mapping")
        merged = dict(_DEFAULT_RULES)
        merged.update(loaded)
        return merged
    except (OSError, ValueError, json.JSONDecodeError):
        return _DEFAULT_RULES


def _risk(level: RiskLevel, *reasons: str) -> RiskResult:
    return RiskResult(level, list(reasons), source="rules")


def _blocked(command: str, rules: dict[str, Any]) -> str | None:
    for pattern in rules.get("blocked_patterns", []):
        try:
            if re.search(pattern, command):
                return f"matches hard-block rule: {pattern}"
        except re.error:
            # A broken local rule must not make a command appear safe.
            return "a safety rule could not be evaluated"
    return None


def _split_shell(command: str) -> list[str]:
    """Split a shell command at unquoted control operators, preserving each part."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|;&")
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
    parts: list[list[str]] = [[]]
    for token in tokens:
        if token and all(ch in "|;&" for ch in token):
            if parts[-1]:
                parts.append([])
        else:
            parts[-1].append(token)
    return [" ".join(part).strip() for part in parts if part]


def _command_name(tokens: list[str]) -> tuple[str, list[str]]:
    """Unwrap common launchers without ever treating their contents as safer."""
    args = list(tokens)
    while args and Path(args[0]).name.lower() in {"env", "command", "builtin"}:
        args.pop(0)
        while args and (args[0].startswith("-") or "=" in args[0]):
            args.pop(0)
    if args and Path(args[0]).name.lower() in {"sudo", "doas", "su"}:
        # Caller handles privilege escalation as sensitive; inspect inner command too.
        args.pop(0)
        while args and args[0].startswith("-"):
            args.pop(0)
    name = Path(args[0]).name.lower() if args else ""
    return name, args


def classify(command: str, ctx: Context) -> RiskResult:
    """Return the highest risk found in a command or any shell-command segment."""
    if not isinstance(command, str) or not command.strip():
        return _risk(RiskLevel.SAFE, "empty command")

    rules = _load_rules()
    blocked_reason = _blocked(command, rules)
    if blocked_reason:
        return _risk(RiskLevel.BLOCKED, blocked_reason)
    # Catch common destructive combinations even when flags are separated or a
    # harmless-looking wrapper (for example `command`) precedes the executable.
    if re.search(r"(?i)\brm\b[^;&|]*\s--no-preserve-root\b", command):
        return _risk(RiskLevel.BLOCKED, "recursive removal explicitly targets the filesystem root")
    for segment in _split_shell(command):
        try:
            parts = shlex.split(segment, posix=True)
        except ValueError:
            parts = []
        if parts:
            executable_index = next((i for i, item in enumerate(parts) if Path(item).name.lower() == "rm"), None)
            if executable_index is not None:
                args = parts[executable_index + 1:]
                flags = [arg for arg in args if arg.startswith("-") and not arg.startswith("--")]
                has_recursive = any("r" in flag[1:].lower() for flag in flags)
                has_force = any("f" in flag[1:].lower() for flag in flags)
                targets_root = any(arg == "/" for arg in args)
                if has_recursive and has_force and targets_root:
                    return _risk(RiskLevel.BLOCKED, "recursive forced removal targets filesystem root")

    try:
        segments = _split_shell(command)
        if not segments:
            return _risk(RiskLevel.SENSITIVE, "command could not be parsed")
        all_results: list[RiskResult] = []
        for segment in segments:
            # Redirection can overwrite files; treat it as sensitive unless hard-blocked above.
            if re.search(r"(?<!\\)[<>]{1,2}", segment):
                all_results.append(_risk(RiskLevel.SENSITIVE, "contains output/input redirection"))
                # Continue to inspect the executable as well.
                segment = re.split(r"(?<!\\)[<>]{1,2}", segment, maxsplit=1)[0].strip()
                if not segment:
                    continue
            try:
                tokens = shlex.split(segment, posix=True)
            except ValueError:
                return _risk(RiskLevel.SENSITIVE, "could not parse command segment")
            if not tokens:
                continue
            first = Path(tokens[0]).name.lower()
            if first == "env" and len(tokens) == 1:
                all_results.append(_risk(RiskLevel.SAFE, "env without arguments only displays the environment"))
                continue
            if first in {"bash", "sh", "zsh", "dash", "cmd", "cmd.exe", "powershell", "pwsh"} and any(
                arg in {"-c", "/c", "-command", "-Command"} for arg in tokens[1:3]
            ):
                all_results.append(_risk(RiskLevel.SENSITIVE, "shell executes an embedded command string"))
                continue
            name, unwrapped = _command_name(tokens)
            if not name:
                all_results.append(_risk(RiskLevel.SENSITIVE, "could not identify executable"))
                continue
            if first in {"sudo", "su", "doas"}:
                all_results.append(_risk(RiskLevel.SENSITIVE, "uses privilege escalation"))
                continue
            safe_names = {x.lower() for x in rules.get("safe_commands", [])}
            moderate_names = {x.lower() for x in rules.get("moderate_commands", [])}
            # Git is a command family: inspection-only subcommands are safe, while
            # operations that change local state remain moderate and remote writes sensitive.
            if name in {"python", "python3", "pytest", "ruff"} and any(arg in {"--version", "-V", "-v"} for arg in unwrapped[1:]):
                result = _risk(RiskLevel.SAFE, f"{name} version query is read-only")
            elif name == "git" and len(unwrapped) > 1 and unwrapped[1].lower() in {"status", "log", "diff", "show", "remote", "rev-parse"}:
                result = _risk(RiskLevel.SAFE, "git inspection subcommand is read-only")
            elif name == "git" and len(unwrapped) > 1 and unwrapped[1].lower() in {"push", "pull", "fetch", "clone"}:
                result = _risk(RiskLevel.SENSITIVE, "git subcommand performs a network operation")
            elif name in safe_names:
                result = _risk(RiskLevel.SAFE, f"{name} is on the read-only allowlist")
            elif name in moderate_names:
                result = _risk(RiskLevel.MODERATE, f"{name} can make reversible changes")
            elif name in {x.lower() for x in rules.get("sensitive_commands", [])}:
                result = _risk(RiskLevel.SENSITIVE, f"{name} can alter system state or access the network")
            else:
                result = _risk(RiskLevel.SENSITIVE, f"{name!r} is not recognized by the safety rules")

            joined = " ".join(unwrapped)
            path_is_sensitive = any(path.lower() in joined.lower() for path in rules.get("sensitive_paths", []))
            path_mutators = {"tee", "chmod", "chown", "mv", "cp", "rm", "touch", "mkdir", "install", "sed", "perl", "python", "python3", "powershell", "pwsh", "reg"}
            if path_is_sensitive and (name in path_mutators or re.search(r"(?<!\\)[>]{1,2}", segment)):
                result = _risk(max(result.level, RiskLevel.SENSITIVE), *result.reasons, "may modify a sensitive system path")
            if ctx.untrusted_seen and (
                name in {x.lower() for x in rules.get("network_commands", [])}
                or any(path.lower() in joined.lower() for path in rules.get("sensitive_paths", []))
                or name in {"apt", "apt-get", "dnf", "yum", "brew", "winget", "systemctl", "chmod", "chown", "reg", "netsh"}
            ):
                result = _risk(max(result.level, RiskLevel.SENSITIVE), *result.reasons, "elevated because untrusted content was seen in context")
            all_results.append(result)

        if not all_results:
            return _risk(RiskLevel.SENSITIVE, "no command could be analyzed")
        highest = max(result.level for result in all_results)
        reasons = [reason for result in all_results if result.level == highest for reason in result.reasons]
        if len(segments) > 1:
            reasons.append("evaluated each shell command segment; overall risk is the highest segment risk")
        return _risk(highest, *dict.fromkeys(reasons))
    except (ValueError, OSError, TypeError):
        return _risk(RiskLevel.SENSITIVE, "unexpected parsing failure; defaulting to sensitive")

