from __future__ import annotations

import shutil
import urllib.parse
from pathlib import Path

import pytest

from termiai.contracts import (
    ApprovalChoice,
    ApprovalRequest,
    ApprovalResponse,
    Context,
    EnvInfo,
    Mode,
    ToolResult,
)
from termiai.journal import Journal
from termiai.pipeline import Pipeline
from termiai.platform import get_adapter
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
        super().__init__()
        self.ran: list[str] = []

    def run(self, args, ctx):
        self.ran.append(args["command"])
        return ToolResult(ok=True, output="spy ok", exit_code=0)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    h.mkdir(parents=True, exist_ok=True)
    return h


@pytest.fixture
def env(home: Path) -> EnvInfo:
    return EnvInfo(
        os="linux",
        os_name="linux",
        distro="Ubuntu",
        distro_version="24.04",
        kernel="6.8.0-45-generic",
        shell="bash",
        package_manager="apt",
        is_root=False,
        home=str(home),
    )


@pytest.fixture
def journal(tmp_path: Path) -> Journal:
    j = Journal(path=tmp_path / "journal.jsonl")
    j.backup_dir = tmp_path / "backups"
    j.backup_dir.mkdir(parents=True, exist_ok=True)
    return j


@pytest.fixture
def ctx(env: EnvInfo, home: Path, journal: Journal) -> Context:
    return Context(
        mode=Mode.ASK_SENSITIVE,
        env=env,
        cwd=home,
        interactive=False,
        journal=journal,
        adapter=get_adapter(env),
    )


@pytest.fixture
def registry():
    reg = default_registry()
    reg.register(SpyShell(), replace=True)
    return reg


def make_pipeline(registry, journal, approver=None, **kw):
    return Pipeline(registry, journal, approver, **kw)


@pytest.fixture
def fake_trash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Replace send2trash with a freedesktop-style Trash inside tmp_path."""
    data_home = tmp_path / "xdg"
    trash = data_home / "Trash"
    (trash / "files").mkdir(parents=True, exist_ok=True)
    (trash / "info").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))

    def fake_send2trash(path: str) -> None:
        src = Path(path)
        name, n = src.name, 1
        while (trash / "files" / name).exists():
            n += 1
            name = f"{src.name}.{n}"
        shutil.move(str(src), str(trash / "files" / name))
        (trash / "info" / f"{name}.trashinfo").write_text(
            f"[Trash Info]\nPath={urllib.parse.quote(str(src))}\nDeletionDate=2026-01-01T00:00:00\n",
            encoding="utf-8",
        )

    import send2trash

    monkeypatch.setattr(send2trash, "send2trash", fake_send2trash)
    return trash


def run_tool(tool, args, ctx):
    """Run a tool the way the Executor would: preview first, then run."""
    pv = tool.preview(args, ctx)
    return pv, tool.run(args, ctx)
