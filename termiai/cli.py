"""MINIMAL CLI for the walking skeleton. OWNED BY M6: replace with typer + rich (PRD F-1..F-4).

python -m termiai --mock "list my Downloads"
python -m termiai --mock --mode ask-all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from termiai import __version__
from termiai.agent import Agent
from termiai.contracts import (
    ApprovalChoice,
    ApprovalRequest,
    ApprovalResponse,
    Context,
    Event,
    Mode,
)
from termiai.env import detect_env
from termiai.journal import Journal
from termiai.llm import demo_script, get_client
from termiai.pipeline import Pipeline
from termiai.tools import default_registry


class ConsoleApprover:
    def ask(self, req: ApprovalRequest) -> ApprovalResponse:
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
    if event.kind == "plan":
        print(f"Agent > Plan: {event.message}")
    elif event.kind == "step_start":
        print(f"Agent > {event.message}")
    elif event.kind == "tool_result":
        print(f"  {'✓' if event.data.get('ok') else '✗'} {event.message}")
    elif event.kind == "verify":
        print(f"  {'✓' if event.data.get('ok') else '✗'} Verified: {event.message}")
    elif event.kind == "replan":
        print(f"Agent > Trying again: {event.message}")


def build_agent(mock: bool, mode: Mode, model: str | None) -> Agent:
    registry = default_registry()
    ctx = Context(mode=mode, env=detect_env())
    journal = Journal(Path.home() / ".termiai" / "journal.jsonl")
    pipeline = Pipeline(registry, journal, ConsoleApprover(), emit=print_event)
    llm = demo_script() if mock else get_client(model)
    return Agent(llm, registry, pipeline, ctx, emit=print_event)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="termiai", description="Prompt your computer.")
    parser.add_argument("prompt", nargs="?", help="what you want done; omit for interactive mode")
    parser.add_argument("--mode", choices=[m.value for m in Mode], default=Mode.ASK_SENSITIVE.value)
    parser.add_argument("--model", help="LLM model name (M6)")
    parser.add_argument("--mock", action="store_true", help="use the scripted demo LLM")
    args = parser.parse_args(argv)

    try:
        agent = build_agent(args.mock, Mode(args.mode), args.model)
    except NotImplementedError as exc:
        print(f"termiai: {exc}", file=sys.stderr)
        return 2

    if args.prompt:
        report = agent.run(args.prompt)
        print(f"\n{'Done' if report.ok else 'Stopped'}: {report.summary}")
        return 0 if report.ok else 1

    print(f"TermiAI v{__version__}   mode: {args.mode}\n")
    while True:
        try:
            prompt = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if prompt in ("/exit", "exit", "quit"):
            return 0
        if prompt:
            report = agent.run(prompt)
            print(f"\n{'Done' if report.ok else 'Stopped'}: {report.summary}\n")
