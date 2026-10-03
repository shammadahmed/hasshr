"""STUB: owned by M2 (Safety engine). Replace the body, keep the signature.

    classify(command: str, ctx: Context) -> RiskResult

This stub is deliberately conservative: anything it does not recognise is SENSITIVE.
"""

from __future__ import annotations

import re
import shlex

from termiai.contracts import Context, RiskLevel, RiskResult

SAFE_COMMANDS = {
    "ls", "cat", "pwd", "df", "du", "ps", "uname", "whoami", "date", "head", "tail",
    "lsblk", "free", "uptime", "aplay", "arecord", "pactl", "lspci", "lsusb", "dmesg",
    "journalctl", "lsmod", "id", "hostname", "stat", "file", "which", "grep",
}  # fmt: skip

BLOCKED_PATTERNS = [
    r"\brm\s+(-\w*\s+)*-\w*r\w*f\w*\s+/(\s|$)",  # rm -rf /
    r"\brm\s+(-\w*\s+)*-\w*f\w*r\w*\s+/(\s|$)",
    r"\bdd\b.*\bof=/dev/(sd|nvme|hd|vd|mmcblk)",
    r"\bmkfs(\.\w+)?\b",
    r":\(\)\s*\{.*\};\s*:",  # fork bomb
]
SHELL_SYNTAX = set("|;&<>`$()")


def classify(command: str, ctx: Context) -> RiskResult:
    for pattern in BLOCKED_PATTERNS:
        if re.search(pattern, command):
            return RiskResult(RiskLevel.BLOCKED, ["matches a hard-blocked pattern"])
    if any(ch in command for ch in SHELL_SYNTAX):
        return RiskResult(RiskLevel.SENSITIVE, ["contains shell syntax the stub cannot parse"])
    try:
        tokens = shlex.split(command)
    except ValueError:
        return RiskResult(RiskLevel.SENSITIVE, ["could not parse command"])
    if not tokens:
        return RiskResult(RiskLevel.SAFE, ["empty command"])
    if tokens[0] == "sudo":
        return RiskResult(RiskLevel.SENSITIVE, ["uses sudo"])
    if tokens[0] in SAFE_COMMANDS:
        return RiskResult(RiskLevel.SAFE, [f"{tokens[0]} is read-only"])
    return RiskResult(RiskLevel.SENSITIVE, [f"'{tokens[0]}' is not in the stub's safe list"])
