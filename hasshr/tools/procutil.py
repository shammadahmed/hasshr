"""Tiny helper to run read-only system commands safely.

Used by the diagnostic tools and snapshot collectors. Commands are run as an
argument list (never through a shell), with no stdin, a hard timeout, and no
privilege escalation.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from .base import ToolError


@dataclass
class Captured:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def have(program: str) -> bool:
    return shutil.which(program) is not None


def run_capture(argv: list[str], timeout: int = 30) -> Captured:
    """Run `argv` and capture output. Raises ToolError if the program is missing
    or times out; a non-zero exit code is returned, not raised."""
    if not argv or not have(argv[0]):
        raise ToolError(f"'{argv[0] if argv else ''}' is not installed on this machine.")
    env = dict(os.environ, LC_ALL="C", PAGER="cat", SYSTEMD_PAGER="", NO_COLOR="1")
    try:
        proc = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              errors="replace", timeout=timeout, env=env)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"'{argv[0]}' took longer than {timeout} seconds and was stopped.") from exc
    except OSError as exc:
        raise ToolError(f"Could not run '{argv[0]}': {exc}") from exc
    return Captured(proc.returncode, proc.stdout, proc.stderr)


def try_capture(argv: list[str], timeout: int = 30) -> Captured | None:
    """Like run_capture but returns None instead of raising (for optional data)."""
    try:
        return run_capture(argv, timeout)
    except ToolError:
        return None
