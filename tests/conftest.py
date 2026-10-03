from __future__ import annotations

import pytest

from termiai.contracts import (
    ApprovalChoice,
    ApprovalRequest,
    ApprovalResponse,
    Context,
    Mode,
    ToolResult,
)
from termiai.journal import Journal
from termiai.pipeline import Pipeline
from termiai.tools import default_registry
from termiai.tools.shell import RunShell


class FakeApprover:
    """Answers from a queue and records every request."""

    def __init__(self, *choices: ApprovalChoice | ApprovalResponse) -> None:
        self.queue = list(choices)
        self.requests: list[ApprovalRequest] = []

    def ask(self, req: ApprovalRequest) -> ApprovalResponse:
        self.requests.append(req)
        item = self.queue.pop(0) if self.queue else ApprovalChoice.NO
        return item if isinstance(item, ApprovalResponse) else ApprovalResponse(item)


class SpyShell(RunShell):
    """Records commands instead of running them, so tests never touch the system."""

    def __init__(self) -> None:
        self.ran: list[str] = []

    def run(self, args, ctx):
        self.ran.append(args["command"])
        return ToolResult(ok=True, output="spy ok", exit_code=0)


@pytest.fixture
def registry():
    reg = default_registry()
    reg.register(SpyShell())
    return reg


@pytest.fixture
def journal():
    return Journal()


def make_pipeline(registry, journal, approver=None, **kw):
    return Pipeline(registry, journal, approver, **kw)


@pytest.fixture
def ctx():
    return Context(mode=Mode.ASK_SENSITIVE)
