"""Tool base class, context, and journal hook protocol.

Every tool:
  * declares a Reversibility label and a plain-language explanation (F-7, F-47)
  * can be previewed BEFORE it runs (`preview`) so the approval prompt can show
    effect + reversibility
  * returns a ToolResult and never raises for ordinary failures
  * routes every system change through the journal hooks (M4)

The Executor (M1) must not call `run` directly: the pipeline is
classify -> decide -> ask -> journal.before -> tool -> journal.after -> verify.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from ..contracts import (
    JournalEntry,
    Reversibility,
    RiskLevel,
    ToolContext,
    ToolResult,
)


@runtime_checkable
class JournalHooks(Protocol):
    """The slice of M4's journal API that tools depend on."""

    def backup_file(self, path: str | Path) -> dict[str, Any]:
        """Back up `path` before it is changed. Return at least
        {"backup": <path>, "sha256": <hex>} (empty dict if the file is absent)."""

    def record(self, entry: Any) -> None:
        """Append an entry to the journal."""


class StubJournal:
    """Safe in-memory stand-in shipped in S1 so teammates can integrate.

    Keeps backups on disk in a temp-style folder so edits are still recoverable
    even before M4's real journal lands.
    """

    def __init__(self, backup_dir: Optional[Path] = None) -> None:
        import tempfile

        self.backup_dir = (
            Path(backup_dir)
            if backup_dir
            else Path(tempfile.mkdtemp(prefix="hasshr-stub-journal-"))
        )
        self.entries: list[JournalEntry] = []
        self.audit_log: list[dict[str, Any]] = []

    def backup_file(self, path: str | Path) -> dict[str, Any]:
        import hashlib
        import shutil

        src = Path(path)
        if not src.is_file():
            return {}
        data = src.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        dest = self.backup_dir / f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}-{src.name}"
        shutil.copy2(src, dest)
        return {"backup": str(dest), "sha256": digest, "original": str(src)}

    def backup(self, path: str | Path) -> Path | None:
        res = self.backup_file(path)
        if isinstance(res, dict) and "backup" in res:
            return Path(res["backup"])
        return None

    def record(self, entry: Any) -> None:
        if isinstance(entry, JournalEntry):
            self.entries.append(entry)
        elif isinstance(entry, dict):
            pass

    def audit(self, event: str, data: dict[str, Any]) -> None:
        self.audit_log.append({"event": event, **data})

    def before(
        self, call: Any, command: str, risk: Any, reversibility: Any, ctx: Any
    ) -> JournalEntry:
        entry = JournalEntry(
            tool=call.name,
            command=command,
            args=dict(call.args),
            risk=int(risk.level),
            reversibility=reversibility.value,
        )
        self.entries.append(entry)
        return entry

    def after(self, entry: JournalEntry, result: Any) -> None:
        entry.ok = result.ok
        entry.output = getattr(result, "output", "")


def _stdin_is_tty() -> bool:
    import sys

    try:
        return bool(sys.stdin and sys.stdin.isatty())
    except (ValueError, OSError):
        return False


@dataclass
class Preview:
    """What the approval prompt shows before a tool runs (F-14, F-47)."""

    explanation: str
    reversibility: Reversibility
    undo_hint: str = ""
    read_only: bool = False
    needs_admin: bool = False
    warnings: list[str] = field(default_factory=list)
    #: Optional longer detail (e.g. a unified diff of a pending edit).
    detail: str = ""


class ToolError(Exception):
    """Raised inside a tool for expected failures; converted to ToolResult(ok=False)."""


class Tool(ABC):
    """Base class for all typed tools."""

    name: str = ""
    description: str = ""
    #: JSON-schema "properties" for the arguments, used for LLM tool calling.
    parameters: dict[str, dict[str, Any]] = {}
    required: tuple[str, ...] = ()
    #: Default reversibility; override `reversibility_for` for argument-dependent cases.
    reversibility: Reversibility = Reversibility.FULL
    read_only: bool = False
    shell_like: bool = False
    default_risk: RiskLevel = RiskLevel.MODERATE

    # ---- schema / validation -------------------------------------------------
    def schema(self) -> dict[str, Any]:
        return self.spec()

    def spec(self) -> dict[str, Any]:
        """Tool definition in the provider-neutral JSON-schema form LiteLLM accepts."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": list(self.required),
                    "additionalProperties": False,
                },
            },
        }

    def validate(self, args: dict[str, Any]) -> Optional[str]:
        """Return an error message if args are invalid, else None."""
        missing = [k for k in self.required if k not in args or args[k] in (None, "")]
        if missing:
            return f"Missing required argument(s): {', '.join(missing)}"
        unknown = [k for k in args if k not in self.parameters]
        if unknown:
            return f"Unknown argument(s): {', '.join(unknown)}"
        type_map = {"string": str, "integer": int, "boolean": bool, "array": list, "number": (int, float)}
        for key, value in args.items():
            expected = self.parameters[key].get("type")
            py = type_map.get(expected)
            if py is None or value is None:
                continue
            if expected == "integer" and isinstance(value, bool):
                return f"Argument '{key}' must be an integer"
            if not isinstance(value, py):
                return f"Argument '{key}' must be of type {expected}"
            enum = self.parameters[key].get("enum")
            if enum and value not in enum:
                return f"Argument '{key}' must be one of: {', '.join(map(str, enum))}"
        return None

    def describe(self, args: dict[str, Any]) -> str:
        import json
        return f"{self.name} {json.dumps(args, sort_keys=True)}"

    def base_risk(self, args: dict[str, Any], ctx: Any = None) -> RiskLevel:
        return RiskLevel.SAFE if self.read_only else self.default_risk

    def with_command(self, call: Any, command: str) -> Any | None:
        return None

    # ---- preview (BEFORE the tool runs) -------------------------------------
    def reversibility_for(self, args: dict[str, Any], ctx: Any = None) -> Reversibility:
        return self.reversibility

    @abstractmethod
    def explain(self, args: dict[str, Any], ctx: ToolContext) -> str:
        """Plain-language description of what this call will do."""

    def undo_hint_for(self, args: dict[str, Any], ctx: ToolContext) -> str:
        return ""

    def preview(self, args: dict[str, Any], ctx: ToolContext) -> Preview:
        return Preview(
            explanation=self.explain(args, ctx),
            reversibility=self.reversibility_for(args, ctx),
            undo_hint=self.undo_hint_for(args, ctx),
            read_only=self.read_only,
        )

    # ---- execution ------------------------------------------------------------
    @abstractmethod
    def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Do the work. May raise ToolError."""

    def run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Validate, execute, and normalise the result. Never raises for tool failures."""
        err = self.validate(args)
        if err:
            return ToolResult(ok=False, error=err, output=err, reversibility=Reversibility.FULL)
        try:
            result = self._run(args, ctx)
        except ToolError as exc:
            return ToolResult(ok=False, error=str(exc), output=str(exc), reversibility=self.reversibility_for(args, ctx))
        except PermissionError as exc:
            msg = f"Permission denied: {exc.filename or exc}"
            return ToolResult(ok=False, error=msg, output=msg, reversibility=self.reversibility_for(args, ctx))
        except OSError as exc:
            msg = f"{type(exc).__name__}: {exc}"
            return ToolResult(ok=False, error=msg, output=msg, reversibility=self.reversibility_for(args, ctx))
        # Always attach the label the user was shown, unless the tool refined it.
        if not result.undo_hint:
            result.undo_hint = self.undo_hint_for(args, ctx)
        return result

    # ---- helpers for subclasses -------------------------------------------------
    def _journal(
        self,
        ctx: ToolContext,
        command: str,
        *,
        backups: list[str] | None = None,
        hashes: dict[str, str] | None = None,
        diff: str = "",
        undo: dict[str, Any] | None = None,
    ) -> JournalEntry:
        """Record a completed state change with the journal (journal.record)."""
        entry = JournalEntry(
            id=uuid.uuid4().hex,
            case_id=ctx.case_id,
            step=ctx.step or self.name,
            command=command,
            backups=backups or [],
            hashes=hashes or {},
            diff=diff,
            time=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            undo=undo or {},
        )
        ctx.journal.record(entry)
        return entry


from .registry import ToolRegistry  # noqa: E402

__all__ = [
    "JournalHooks",
    "Preview",
    "StubJournal",
    "Tool",
    "ToolContext",
    "ToolError",
    "ToolRegistry",
]


