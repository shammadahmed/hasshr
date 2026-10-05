"""Administrator-rights handling (F-24).

The password is typed by the user straight into the OS prompt. We never read,
log, store or forward it:

  * `sudo -v` is run with stdin/stdout/stderr INHERITED from the user's terminal,
    so sudo opens /dev/tty itself; nothing passes through our pipes.
  * Afterwards sudo's own credential cache (a few minutes) lets the actual
    command run non-interactively with captured output.
  * `sudo -S` / `--stdin` (password on stdin) is refused by run_shell.
"""

from __future__ import annotations

import shutil
import subprocess

from .base import ToolContext, ToolError


def sudo_ticket_valid() -> bool:
    """True if sudo would run right now without asking for a password."""
    if shutil.which("sudo") is None:
        return False
    try:
        return subprocess.run(
            ["sudo", "-n", "true"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=15,
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def ensure_admin(ctx: ToolContext) -> None:
    """Make sure privileged commands can run, prompting the user directly if needed.

    Raises ToolError (with a plain-language message) when that is impossible.
    """
    if ctx.env.is_root:
        return
    if ctx.env.os == "windows":
        raise ToolError("This step needs administrator rights. Close TermiAI and start it again from a terminal opened with 'Run as administrator'.")
    if shutil.which("sudo") is None:
        raise ToolError("This step needs administrator rights, but 'sudo' is not installed on this machine.")
    if sudo_ticket_valid():
        return
    if not ctx.interactive:
        raise ToolError("This step needs administrator rights, but there is no interactive terminal to ask for your password. Run TermiAI in a normal terminal window.")
    print("\nThis step needs administrator rights. Your password goes directly to the system prompt; TermiAI never sees it.", flush=True)
    rc = subprocess.run(["sudo", "-v"]).returncode  # inherits the terminal on purpose
    if rc != 0:
        raise ToolError("Administrator authentication failed or was cancelled, so nothing was changed.")
