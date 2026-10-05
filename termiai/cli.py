"""CLI and Terminal UX for TermiAI (M6)."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from termiai import __version__
from termiai.agent import Agent
from termiai.cases import CaseEngine, resume_case, undo_case, watch_case
from termiai.config import load_config, save_config
from termiai.contracts import (
    ApprovalChoice,
    ApprovalRequest,
    ApprovalResponse,
    Context,
    Event,
    Mode,
)
from termiai.env import detect_env
from termiai.journal import Journal, UndoManager
from termiai.llm import demo_script, get_client
from termiai.pipeline import Pipeline
from termiai.tools.registry import default_registry

try:
    from rich.console import Console
    from rich.panel import Panel
    _HAS_RICH = True
    console = Console()
except ImportError:
    _HAS_RICH = False
    console = None


def show_banner() -> None:
    if _HAS_RICH and console:
        console.print(Panel.fit(
            f"[bold cyan]TermiAI[/] [dim]v{__version__}[/]\n"
            "[green]Natural Language Agentic CLI with verified safety controls[/]",
            border_style="cyan"
        ))
    else:
        print(f"=== TermiAI v{__version__} - Natural Language CLI ===")


class RichApprover:
    def ask(self, req: ApprovalRequest) -> ApprovalResponse:
        if _HAS_RICH and console:
            console.print(f"\n[bold yellow]Proposed Command:[/] [cyan]{req.command}[/]")
            if req.explanation:
                console.print(f"[bold dim]Why:[/] {req.explanation}")
            risk_color = "red" if req.risk.level.name in ("BLOCKED", "SENSITIVE") else "yellow"
            console.print(f"[bold]Risk:[/] [{risk_color}]{req.risk.level.name}[/]   [bold]Undo:[/] [green]{req.reversibility.value}[/]")
        else:
            print(f"\n  Command:  {req.command}")
            if req.explanation:
                print(f"  Why:      {req.explanation}")
            print(f"  Risk:     {req.risk.level.name}   Undo: {req.reversibility.value}")

        while True:
            ans = input("  Run this? [y]es  [n]o  [e]dit  [a]lways this session: ").strip().lower()
            if ans in ("y", "yes"):
                return ApprovalResponse(ApprovalChoice.YES)
            if ans in ("n", "no", ""):
                return ApprovalResponse(ApprovalChoice.NO)
            if ans in ("a", "always"):
                return ApprovalResponse(ApprovalChoice.ALWAYS)
            if ans in ("e", "edit"):
                return ApprovalResponse(ApprovalChoice.EDIT, input("  New command: ").strip())


def print_event(event: Event) -> None:
    ok = event.data.get("ok", True)
    prefix = "✓" if ok else "✗"
    if _HAS_RICH and console:
        color_pfx = f"[bold green]{prefix}[/]" if ok else f"[bold red]{prefix}[/]"
        if event.kind == "plan":
            console.print(f"[bold blue]Agent >[/] [bold]Plan:[/] {event.message}")
        elif event.kind in ("step_start", "case_start"):
            console.print(f"[bold blue]Agent >[/] {event.message}")
        elif event.kind == "tool_result":
            console.print(f"  {color_pfx} {event.message}")
        elif event.kind == "verify":
            console.print(f"  {color_pfx} [bold]Verified:[/] {event.message}")
        elif event.kind == "replan":
            console.print(f"[bold yellow]Agent > Trying again:[/] {event.message}")
        elif event.kind == "done":
            console.print(f"[bold green]Agent > Done:[/] {event.message}")
    else:
        if event.kind == "plan":
            print(f"Agent > Plan: {event.message}")
        elif event.kind in ("step_start", "case_start"):
            print(f"Agent > {event.message}")
        elif event.kind == "tool_result":
            print(f"  {prefix} {event.message}")
        elif event.kind == "verify":
            print(f"  {prefix} Verified: {event.message}")
        elif event.kind == "replan":
            print(f"Agent > Trying again: {event.message}")
        elif event.kind == "done":
            print(f"Agent > Done: {event.message}")


def build_agent(mock: bool, mode: Mode, model: str | None) -> Agent:
    registry = default_registry()
    ctx = Context(mode=mode, env=detect_env())
    journal = Journal(Path.home() / ".termiai" / "journal.jsonl")
    pipeline = Pipeline(registry, journal, RichApprover(), emit=print_event)
    llm = demo_script() if mock else get_client(model)
    return Agent(llm, registry, pipeline, ctx, emit=print_event)


def handle_case_command(args: Any) -> int:
    problem = args.problem
    success_test = getattr(args, "test", None)
    if not success_test:
        success_test = input("Define a measurable success test command: ").strip()
    if not success_test:
        print("A success test is required to start a Troubleshooting Case.")
        return 1
    engine = CaseEngine(ctx=Context(), emit=print_event)
    state = engine.run(problem, success_test)
    return 0 if state.outcome and state.outcome.value == "fixed" else 1


def handle_resume_command(args: Any) -> int:
    res = resume_case()
    if res:
        print(f"Resumed Case {res.id}. Outcome: {res.outcome.value if res.outcome else 'in progress'}")
        return 0
    print("No case pending resume after reboot.")
    return 1


def handle_undo_command(args: Any) -> int:
    action_or_case = getattr(args, "id", None)
    if action_or_case and action_or_case.startswith("CASE-"):
        res = undo_case(action_or_case)
        if isinstance(res, dict):
            print(res.get("message", "Rollback completed."))
            return 0 if res.get("ok") else 1
        print("Rollback completed." if res else "Rollback failed.")
        return 0 if res else 1
    mgr = UndoManager()
    try:
        res = mgr.undo(action_or_case)
        print(f"Undid action {res.action_id}: {res.result_summary}")
        return 0
    except Exception as exc:
        print(f"Undo failed: {exc}")
        return 1


def handle_history_command(args: Any) -> int:
    j = Journal(Path.home() / ".termiai" / "journal.jsonl")
    rows = j.history(limit=15)
    if not rows:
        print("No recorded actions in journal.")
        return 0
    print("\nRecent Actions:")
    for r in rows:
        cmd = r.get("command") or r.get("operation") or r.get("tool")
        t = r.get("time") or time.strftime("%H:%M:%S", time.localtime(r.get("timestamp", 0)))
        print(f"  [{t}] {r.get('id', r.get('action_id', ''))} - {cmd} (ok: {r.get('ok')})")
    return 0


def handle_watch_command(args: Any) -> int:
    res = watch_case(getattr(args, "case_id", None))
    print(res.get("message", "Watch check completed."))
    return 0


def handle_config_command(args: Any) -> int:
    cfg = load_config()
    if getattr(args, "key", None) and getattr(args, "val", None):
        cfg[args.key] = args.val
        save_config(cfg)
        print(f"Updated config: {args.key} = {args.val}")
    else:
        print("\nCurrent Configuration (~/.termiai/config.json):")
        for k, v in cfg.items():
            print(f"  {k}: {v}")
    return 0


def is_troubleshooting_prompt(prompt: str) -> bool:
    p = prompt.lower()
    triggers = ("fix ", "diagnose ", "troubleshoot ", "repair ", "dummy output", "alsa force-reload")
    return any(p.startswith(t) or t in p for t in triggers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="termiai", description="Prompt your computer.")
    parser.add_argument("prompt", nargs="?", help="what you want done; omit for interactive mode")
    parser.add_argument("--mode", choices=[m.value for m in Mode], default=Mode.ASK_SENSITIVE.value)
    parser.add_argument("--model", help="LLM model name (M6)")
    parser.add_argument("--mock", action="store_true", help="use the scripted demo LLM")

    subparsers = parser.add_subparsers(dest="subcommand")

    case_parser = subparsers.add_parser("case", help="Start or manage a troubleshooting Case")
    case_parser.add_argument("problem", nargs="?", help="Problem description")
    case_parser.add_argument("--test", help="Measurable success test command")
    case_parser.add_argument("action", nargs="?", help="undo [case_id]")
    case_parser.add_argument("target_id", nargs="?", help="case ID for undo")

    subparsers.add_parser("resume", help="Resume an active Case after reboot")

    undo_parser = subparsers.add_parser("undo", help="Revert the last change or specific action/case")
    undo_parser.add_argument("id", nargs="?", help="Action ID or Case ID")

    subparsers.add_parser("history", help="Show recent action history and journal audit trail")

    watch_parser = subparsers.add_parser("watch", help="Check upstream for fixes to known issues")
    watch_parser.add_argument("case_id", nargs="?", help="Case ID to watch")

    config_parser = subparsers.add_parser("config", help="View or modify TermiAI configuration")
    config_parser.add_argument("key", nargs="?", help="Config key to set")
    config_parser.add_argument("val", nargs="?", help="Config value to set")

    args = parser.parse_args(argv)

    if args.subcommand == "case":
        if args.problem == "undo":
            args.id = args.test or args.action or args.target_id
            return handle_undo_command(args)
        if not args.problem:
            print("Usage: termiai case \"<problem>\" [--test \"<command>\"]")
            return 1
        return handle_case_command(args)
    elif args.subcommand == "resume":
        return handle_resume_command(args)
    elif args.subcommand == "undo":
        return handle_undo_command(args)
    elif args.subcommand == "history":
        return handle_history_command(args)
    elif args.subcommand == "watch":
        return handle_watch_command(args)
    elif args.subcommand == "config":
        return handle_config_command(args)

    try:
        agent = build_agent(args.mock, Mode(args.mode), args.model)
    except NotImplementedError as exc:
        print(f"termiai: {exc}", file=sys.stderr)
        return 2

    if args.prompt:
        report = agent.run(args.prompt)
        print(f"\n{'Done' if report.ok else 'Stopped'}: {report.summary}")
        return 0 if report.ok else 1

    show_banner()
    print(f"Mode: {args.mode}   (Type /help for commands, /exit to quit)\n")
    while True:
        try:
            prompt = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if prompt in ("/exit", "exit", "quit"):
            return 0
        if prompt in ("/help", "help"):
            print("\nCommands:")
            print("  /history        - View recent actions in journal")
            print("  /undo [id]      - Undo last action or specific ID")
            print("  /case <problem> - Start a Troubleshooting Case")
            print("  /resume         - Resume case after reboot")
            print("  /watch          - Check upstream fixes for known issues")
            print("  /exit           - Exit TermiAI\n")
            continue
        if prompt.startswith("/case "):
            prob = prompt[6:].strip()
            st = input("Success test command: ").strip()
            if st:
                engine = CaseEngine(ctx=agent.ctx, emit=print_event)
                engine.run(prob, st)
            continue
        if prompt.startswith("/undo"):
            parts = prompt.split()
            target_id = parts[1] if len(parts) > 1 else None
            handle_undo_command(argparse.Namespace(id=target_id))
            continue
        if prompt == "/history":
            handle_history_command(argparse.Namespace())
            continue
        if prompt == "/resume":
            handle_resume_command(argparse.Namespace())
            continue
        if prompt:
            report = agent.run(prompt)
            print(f"\n{'Done' if report.ok else 'Stopped'}: {report.summary}\n")
    return 0
