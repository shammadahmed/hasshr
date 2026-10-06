"""run_shell: the general-purpose tool for everything typed tools do not cover.

Safety properties implemented here (the Risk Engine, M2, still decides whether
a command may run at all):

  * Hard timeout with process-tree cleanup; captured stdout/stderr and exit code.
  * Administrator commands: the password is typed by the user directly into the
    OS prompt (privilege.ensure_admin). It never passes through the LLM, our
    pipes, or the logs (F-24). `sudo -S` / `--stdin` is refused.
  * Interactive programs (nano, vim, less, top ...) are refused; the agent must
    use edit_file / read_file instead (F-37).
  * Output is returned marked as UNTRUSTED data (F-26) and is truncated to keep
    the LLM context small.
  * Every state-changing command is recorded through the journal hooks, with
    its reversibility label, so rollback can warn about steps it cannot undo.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from typing import Any, Optional

from ..contracts import Reversibility, RiskLevel, ToolResult
from .base import Preview, Tool, ToolContext, ToolError
from .privilege import ensure_admin
from .shell_meta import ShellAnalysis, analyse_command

DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 900
MAX_STREAM_CHARS = 20_000
UNSUPPORTED_PRIVILEGE = {"su", "doas", "pkexec", "run0"}


def truncate_middle(text: str, limit: int = MAX_STREAM_CHARS) -> tuple[str, bool]:
    """Keep the start and end of long output (errors are usually at the end)."""
    if len(text) <= limit:
        return text, False
    half = limit // 2
    omitted = len(text) - 2 * half
    return f"{text[:half]}\n[... {omitted} characters omitted ...]\n{text[-half:]}", True


def _kill_tree(proc: subprocess.Popen[str], own_session: bool) -> None:
    """Stop a timed-out command and (where possible) everything it started."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10)
        elif own_session:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
        else:
            # Shares the user's terminal session (needed for sudo): signal only our child.
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass


class RunShell(Tool):
    name = "run_shell"
    description = (
        "Run a shell command when no typed tool fits (package managers, services, system settings, "
        "diagnostics). Always include a plain-language explanation. Do not use interactive programs "
        "(nano, vim, less, top) and do not put passwords in commands; use edit_file/read_file for files."
    )
    parameters = {
        "command": {"type": "string", "description": "The exact command to run."},
        "explanation": {"type": "string", "description": "One plain-language sentence telling the user what this does and why."},
        "timeout_seconds": {"type": "integer", "description": f"Stop the command after this many seconds (default {DEFAULT_TIMEOUT}, max {MAX_TIMEOUT})."},
        "cwd": {"type": "string", "description": "Folder to run in (default: the current folder)."},
    }
    required = ("command", "explanation")
    reversibility = Reversibility.PARTIAL

    shell_like = True

    def describe(self, args: dict[str, Any]) -> str:
        return str(args.get("command") or "")

    def base_risk(self, args: dict[str, Any], ctx: Any = None) -> RiskLevel:
        if not args.get("command"):
            return RiskLevel.SAFE
        a = self._analyse(args, ctx)
        return RiskLevel.SAFE if a.read_only else RiskLevel.MODERATE

    def with_command(self, call: Any, command: str) -> Any:
        from dataclasses import is_dataclass, replace
        new_args = dict(getattr(call, "args", {}))
        new_args["command"] = command
        if is_dataclass(call):
            return replace(call, args=new_args)
        call.args = new_args
        return call

    # ---- analysis helpers ------------------------------------------------------
    def _analyse(self, args: dict[str, Any], ctx: Any = None) -> ShellAnalysis:
        cwd = str(ctx.resolve(args.get("cwd") or ".")) if ctx and hasattr(ctx, "resolve") else (args.get("cwd") or ".")
        return analyse_command(args.get("command", ""), cwd)

    def reversibility_for(self, args: dict[str, Any], ctx: Any = None) -> Reversibility:
        return self._analyse(args, ctx).reversibility if args.get("command") else Reversibility.PARTIAL

    def explain(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return str(args.get("explanation") or f"Run: {args.get('command')}")

    def undo_hint_for(self, args: dict[str, Any], ctx: ToolContext) -> str:
        if not args.get("command"):
            return ""
        a = self._analyse(args, ctx)
        if a.read_only:
            return ""
        return {
            Reversibility.FULL: "This change can be reversed.",
            Reversibility.PARTIAL: "Undo may be incomplete; check the journal after it runs.",
            Reversibility.NONE: "Undo is NOT available. Consider a backup or system snapshot first.",
        }[a.reversibility]

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        a = self._analyse(args, ctx)
        pv = Preview(
            explanation=self.explain(args, ctx),
            reversibility=Reversibility.FULL if a.read_only else a.reversibility,
            undo_hint=self.undo_hint_for(args, ctx),
            read_only=a.read_only,
            needs_admin=a.needs_admin,
            warnings=list(a.reasons) if not a.read_only else [],
        )
        if a.interactive_programs:
            pv.warnings.append(f"Interactive program(s) cannot be used: {', '.join(sorted(set(a.interactive_programs)))}. Use edit_file or read_file.")
        if a.privilege_stdin:
            pv.warnings.append("Passing a password on the command line (sudo -S) is not allowed.")
        if a.needs_admin:
            pv.warnings.append("Administrator rights are needed; you will be asked for your password in the terminal.")
        return pv

    def validate(self, args: dict[str, Any]) -> Optional[str]:
        err = super().validate(args)
        if err:
            return err
        timeout = args.get("timeout_seconds")
        if timeout is not None and not (1 <= int(timeout) <= MAX_TIMEOUT):
            return f"timeout_seconds must be between 1 and {MAX_TIMEOUT}"
        if not str(args["command"]).strip():
            return "command must not be empty"
        return None

    # ---- execution ---------------------------------------------------------------
    def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        command = str(args["command"])
        analysis = self._analyse(args, ctx)

        if analysis.interactive_programs:
            progs = ", ".join(sorted(set(analysis.interactive_programs)))
            raise ToolError(f"'{progs}' is an interactive program and cannot be driven by the agent. Use read_file to view a file or edit_file to change one.")
        if analysis.privilege_stdin:
            raise ToolError("Passing a password to sudo (-S/--stdin) is not allowed. Hasshr asks for your password directly in the terminal instead.")
        if any(s.via_privilege in UNSUPPORTED_PRIVILEGE or s.program in UNSUPPORTED_PRIVILEGE for s in analysis.segments):
            raise ToolError("Only 'sudo' is supported for administrator commands (su/doas/pkexec cannot ask for the password safely).")

        cwd = ctx.resolve(args.get("cwd") or ".")
        if not cwd.is_dir():
            raise ToolError(f"Folder not found: {cwd}")
        timeout = int(args.get("timeout_seconds") or DEFAULT_TIMEOUT)

        uses_sudo = analysis.needs_admin and not ctx.env.is_root
        if analysis.needs_admin:
            ensure_admin(ctx)  # prompts the user directly; the wait is not counted against the timeout

        argv = ctx.adapter.shell_argv(command)  # type: ignore[union-attr]
        env = dict(os.environ)
        env.update({"PAGER": "cat", "GIT_PAGER": "cat", "SYSTEMD_PAGER": "", "NO_COLOR": "1", "TERM": env.get("TERM", "dumb")})

        own_session = os.name != "nt" and not uses_sudo
        popen_kwargs: dict[str, Any] = {}
        if own_session:
            popen_kwargs["start_new_session"] = True
        elif os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)

        started = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.Popen(
                argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, errors="replace", **popen_kwargs,
            )
        except OSError as exc:
            raise ToolError(f"Could not start the command: {exc}") from exc
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(proc, own_session)
            try:
                stdout, stderr = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = "", ""
        duration = round(time.monotonic() - started, 2)
        code = proc.returncode

        out_text, out_cut = truncate_middle(stdout or "")
        err_text, err_cut = truncate_middle(stderr or "")
        combined = out_text
        if err_text.strip():
            combined += ("\n" if combined and not combined.endswith("\n") else "") + "[stderr]\n" + err_text
        if timed_out:
            combined += f"\n[stopped: the command ran longer than {timeout} seconds]"

        rev = Reversibility.FULL if analysis.read_only else analysis.reversibility
        if not analysis.read_only:
            self._journal(ctx, command, undo={
                "op": "shell", "command": command, "exit_code": code, "timed_out": timed_out,
                "reversibility": rev.value, "needs_admin": analysis.needs_admin, "cwd": str(cwd),
                "reasons": analysis.reasons,
            })

        ok = (code == 0) and not timed_out
        error = None
        if timed_out:
            error = f"Command timed out after {timeout} seconds and was stopped."
        elif code != 0:
            error = f"Command failed with exit code {code}."
        return ToolResult(
            ok=ok, output=combined.strip("\n"), exit_code=code, error=error, reversibility=rev,
            undo_hint=self.undo_hint_for(args, ctx),
            data={"stdout": out_text, "stderr": err_text, "timed_out": timed_out, "duration": duration,
                  "truncated": out_cut or err_cut, "read_only": analysis.read_only, "needs_admin": analysis.needs_admin,
                  "untrusted": True},
        )
