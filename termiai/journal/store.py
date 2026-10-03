"""STUB: owned by M4 (Journal, undo & audit). Replace the body, keep the public API.

Public API used by the pipeline:
    before(call, command, risk, reversibility, ctx) -> JournalEntry
    after(entry, result) -> None
    audit(event, data) -> None
    backup_file(path) -> str | None      # called by file tools before editing/deleting
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from termiai.contracts import (
    Context,
    JournalEntry,
    Reversibility,
    RiskResult,
    ToolCall,
    ToolResult,
)


class Journal:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.entries: list[JournalEntry] = []
        self.audit_log: list[dict[str, Any]] = []

    def before(
        self,
        call: ToolCall,
        command: str,
        risk: RiskResult,
        reversibility: Reversibility,
        ctx: Context,
    ) -> JournalEntry:
        entry = JournalEntry(
            case_id=ctx.case_id,
            tool=call.name,
            command=command,
            args=dict(call.args),
            risk=int(risk.level),
            reversibility=reversibility.value,
        )
        self.entries.append(entry)
        return entry

    def after(self, entry: JournalEntry, result: ToolResult) -> None:
        entry.ok = result.ok
        entry.output = result.output[:2000]
        self._write({"type": "entry", **asdict(entry)})

    def audit(self, event: str, data: dict[str, Any]) -> None:
        record = {"type": "audit", "event": event, "time": time.time(), **data}
        self.audit_log.append(record)
        self._write(record)

    def backup_file(self, path: str) -> str | None:  # M4: implement (copy + sha256 + diff)
        return None

    def _write(self, record: dict[str, Any]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
