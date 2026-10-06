"""Shared data structures and interfaces for Hasshr.

OWNER: M1 (admin). Every module codes against this file.
RULE: do not change a field or signature here without announcing it in the team
chat and getting M1's approval. Adding a new optional field is fine.

The execution pipeline (no tool may bypass it):
    classify -> decide -> (ask user) -> journal.before -> tool -> journal.after -> verify
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, IntEnum
from pathlib import Path
from typing import Any, Protocol


def new_id() -> str:
    return uuid.uuid4().hex[:8]


# --------------------------------------------------------------------------- enums


class RiskLevel(IntEnum):
    """Ordered so that max() picks the riskier level."""

    SAFE = 0  # read-only
    MODERATE = 1  # reversible changes
    SENSITIVE = 2  # sudo, rm -r, packages, services, system files...
    BLOCKED = 3  # refused in every mode


Level = RiskLevel


class Mode(str, Enum):
    ASK_ALL = "ask-all"
    ASK_SENSITIVE = "ask-sensitive"  # default
    AUTO = "auto"
    PARANOID = "paranoid"
    PLAN_ONLY = "plan-only"


class Reversibility(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"


class Action(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    REFUSE = "refuse"
    EXECUTE = "execute"
    PLAN = "plan"


class ApprovalChoice(str, Enum):
    YES = "yes"
    NO = "no"
    EDIT = "edit"
    ALWAYS = "always"  # for this session


class Confidence(str, Enum):
    CONFIRMED = "confirmed"  # tested on this machine
    LIKELY = "likely"  # matches a report + this machine's hardware/kernel
    SPECULATION = "speculation"


class Outcome(str, Enum):
    FIXED = "fixed"
    WORKAROUND = "workaround"
    KNOWN_ISSUE = "known_issue"  # known upstream bug, no fix available
    UNRESOLVED = "unresolved"


class CasePhase(str, Enum):
    DEFINE_TEST = "define_test"
    DIAGNOSE = "diagnose"
    PLAN = "plan"
    ATTEMPT = "attempt"
    CONFIRM = "confirm"
    REPORT = "report"
    DONE = "done"


# --------------------------------------------------------------------- environment


@dataclass
class EnvInfo:
    os: str = "unknown"
    os_name: str = "unknown"  # linux / darwin / windows
    os_version: str = ""
    distro: str = "unknown"
    distro_version: str = "unknown"
    kernel: str = "unknown"
    shell: str = "unknown"
    package_manager: str | None = None
    is_root: bool = False
    home: str = "~"
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.os and (not self.os_name or self.os_name == "unknown"):
            self.os_name = self.os
        elif self.os_name and (not self.os or self.os == "unknown"):
            self.os = self.os_name

    def summary(self) -> str:
        parts = [f"OS: {self.os_name or self.os}"]
        if self.distro and self.distro != "unknown":
            parts.append(f"distro: {self.distro} {self.distro_version}".strip())
        if self.kernel and self.kernel != "unknown":
            parts.append(f"kernel: {self.kernel}")
        parts.append(f"shell: {self.shell or 'unknown'}")
        parts.append(f"package manager: {self.package_manager or 'none detected'}")
        return "; ".join(parts)

    @classmethod
    def detect(cls) -> EnvInfo:
        try:
            from hasshr.platform.detect import detect_environment
            return detect_environment()
        except Exception:
            return cls()


@dataclass
class Context:
    """Passed through the whole pipeline."""

    mode: Mode = Mode.ASK_SENSITIVE
    env: EnvInfo = field(default_factory=EnvInfo.detect)
    case_id: str | None = None
    always_allow: set[str] = field(default_factory=set)  # "[a]lways this session"
    untrusted_seen: bool = False  # set once web/file content entered the LLM context
    allow_dangerous: bool = False  # reserved; hard blocks stay on in v0.1
    adapter: Any = None
    journal: Any = None
    cwd: Path = field(default_factory=Path.cwd)
    step: str = ""
    step_num: int = 0
    interactive: bool = True
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    dry_run: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.adapter is None:
            try:
                from hasshr.platform import get_adapter
                self.adapter = get_adapter(self.env)
            except Exception:
                self.adapter = None
        if self.journal is None:
            try:
                from hasshr.tools.base import StubJournal
                self.journal = StubJournal()
            except Exception:
                self.journal = None

    def resolve(self, path: str | Path) -> Path:
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.cwd / p
        return p


ToolContext = Context


# ------------------------------------------------------------------------- tools


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""  # plain-language reason shown to the user
    id: str = field(default_factory=new_id)
    llm_risk: RiskLevel | None = None  # the LLM's own rating; it can only raise risk


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    exit_code: int | None = None
    error: str | None = None
    declined: bool = False  # user said no
    refused: bool = False  # blocked by the safety engine
    data: dict[str, Any] = field(default_factory=dict)
    reversibility: Reversibility = Reversibility.FULL
    undo_hint: str = ""


# ------------------------------------------------------------------------ safety


@dataclass
class RiskResult:
    level: RiskLevel
    reasons: list[str] = field(default_factory=list)
    source: str = "rules"  # "rules" | "llm"


@dataclass
class Decision:
    action: Action
    mode: Mode
    reason: str = ""


@dataclass
class ApprovalRequest:
    call: ToolCall
    command: str  # exact command / description of what will run
    risk: RiskResult
    reversibility: Reversibility
    explanation: str = ""


@dataclass
class ApprovalResponse:
    choice: ApprovalChoice
    edited_command: str | None = None


class Approver(Protocol):
    """Implemented by the CLI (M6). Tests use fakes."""

    def ask(self, req: ApprovalRequest) -> ApprovalResponse: ...


# ----------------------------------------------------------------------- journal


@dataclass
class JournalEntry:
    id: str = field(default_factory=new_id)
    case_id: str | None = None
    tool: str = ""
    command: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    risk: int = 0
    reversibility: str = Reversibility.FULL.value
    timestamp: float = field(default_factory=time.time)
    time: str = ""
    step: str = ""
    backups: Any = field(default_factory=list)  # original path -> backup path or list
    hashes: dict[str, str] = field(default_factory=dict)  # path -> sha256 before/after
    diff: str = ""
    ok: bool | None = None
    output: str = ""
    undo: dict[str, Any] = field(default_factory=dict)


@dataclass
class Snapshot:
    packages: dict[str, str] = field(default_factory=dict)
    services: dict[str, str] = field(default_factory=dict)
    module_params: dict[str, str] = field(default_factory=dict)
    file_hashes: dict[str, str] = field(default_factory=dict)
    taken_at: float = field(default_factory=time.time)


# ------------------------------------------------------------------------- cases


@dataclass
class Hypothesis:
    change: str
    rationale: str = ""
    reversibility: Reversibility = Reversibility.FULL
    reboot_needed: bool = False
    source: str = "own reasoning"  # or "official docs" / a URL (untrusted)


@dataclass
class Finding:
    statement: str
    confidence: Confidence = Confidence.SPECULATION
    evidence: list[str] = field(default_factory=list)


@dataclass
class CaseState:
    id: str = field(default_factory=new_id)
    problem: str = ""
    success_test: str | None = None
    phase: CasePhase = CasePhase.DEFINE_TEST
    hypotheses: list[Hypothesis] = field(default_factory=list)
    active_hypothesis: Hypothesis | None = None
    winning_hypothesis: Hypothesis | None = None
    attempts: int = 0
    findings: list[Finding] = field(default_factory=list)
    outcome: Outcome | None = None
    reboot_pending: bool = False
    report_path: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


# -------------------------------------------------------------------- agent loop


@dataclass
class PlanStep:
    description: str


@dataclass
class Plan:
    goal: str
    steps: list[PlanStep] = field(default_factory=list)


@dataclass
class StepResult:
    step: PlanStep
    ok: bool
    summary: str = ""
    tool_results: list[tuple[ToolCall, ToolResult]] = field(default_factory=list)
    declined: bool = False
    refused: bool = False
    budget_exhausted: bool = False


@dataclass
class Verdict:
    ok: bool
    notes: str = ""
    parseable: bool = True


@dataclass
class RunReport:
    ok: bool
    summary: str = ""
    attempts: int = 0
    plan: Plan | None = None
    verdict: Verdict | None = None
    stopped_reason: str | None = None
    steps_used: int = 0


@dataclass
class Event:
    """Emitted by the pipeline and agent; the CLI (M6) renders these."""

    kind: str  # plan | step_start | step_done | decision | tool_start | tool_result | verify | replan | done
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- LLM


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(Protocol):
    """Implemented by M6 (LiteLLM). Messages use the OpenAI chat format."""

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResponse: ...


def assistant_message(resp: LLMResponse) -> dict[str, Any]:
    """Convert an LLMResponse into an OpenAI-format assistant message."""
    msg: dict[str, Any] = {"role": "assistant", "content": resp.content}
    if resp.tool_calls:
        msg["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                "function": {"name": c.name, "arguments": json.dumps(c.args)},
            }
            for c in resp.tool_calls
        ]
    return msg
