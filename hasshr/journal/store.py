"""Journal, undo & audit (M4). Full implementation with snapshots and rollback.

Public API used by the pipeline:
    before(call, command, risk, reversibility, ctx) -> JournalEntry
    after(entry, result) -> None
    audit(event, data) -> None
    backup_file(path) -> dict[str, Any] | str | None
    record(entry) -> None
    history(limit=20, case_id=None) -> list[dict[str, Any]]
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from hasshr.contracts import (
    Context,
    JournalEntry,
    Reversibility,
    RiskResult,
    ToolCall,
    ToolResult,
)
from hasshr.journal.journal import redact
from hasshr.journal.snapshot import SnapshotManager


class Journal:
    def __init__(
        self,
        path: Path | None = None,
        base_dir: Path | None = None,
        backup_dir: Path | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.entries: list[JournalEntry] = []
        self.audit_log: list[dict[str, Any]] = []
        self.base_dir = (
            Path(base_dir)
            if base_dir
            else (self.path.parent if self.path else Path.home() / ".hasshr")
        )
        self.snapshot_mgr = SnapshotManager(base_dir=self.base_dir)
        self.backup_dir = (
            Path(backup_dir) if backup_dir else (self.base_dir / "backups")
        )
        self.backup_dir.mkdir(parents=True, exist_ok=True)

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
        # If call mentions a file or path to modify, automatically backup
        path_arg = call.args.get("path") or call.args.get("target") or call.args.get("src")
        if path_arg and reversibility != Reversibility.NONE:
            p = Path(path_arg).expanduser()
            if p.is_file():
                b_info = self.backup_file(p)
                if isinstance(b_info, dict):
                    entry.backups = {str(p): b_info.get("backup", "")}
                    if "sha256" in b_info:
                        entry.hashes[str(p)] = b_info["sha256"]
        self.entries.append(entry)
        self._write({"type": "before", **asdict(entry)})
        return entry

    def after(self, entry: JournalEntry, result: ToolResult) -> None:
        entry.ok = result.ok
        entry.output = redact(result.output[:4000]) if result.output else ""
        self._write({"type": "entry", **asdict(entry), "ok": entry.ok, "output": entry.output})

    def audit(self, event: str, data: dict[str, Any]) -> None:
        clean_data = {}
        for k, v in data.items():
            if any(s in k.lower() for s in ("token", "password", "secret", "api_key", "key")):
                clean_data[k] = "[REDACTED]"
            elif isinstance(v, str):
                clean_data[k] = redact(v)
            else:
                clean_data[k] = v
        record = {"type": "audit", "event": event, "time": time.time(), **clean_data}
        self.audit_log.append(record)
        self._write(record)

    def record(self, entry: Any, details: dict[str, Any] | None = None) -> None:
        """Append an entry recorded by tools directly (M3 JournalHooks)."""
        if isinstance(entry, JournalEntry):
            self.entries.append(entry)
            self._write({"type": "tool_record", **asdict(entry)})
        elif isinstance(entry, str):
            self._write({"type": "tool_record", "action": entry, "details": details or {}})
        elif isinstance(entry, dict):
            self._write({"type": "tool_record", **entry})

    def backup(self, path: str | Path) -> Path | None:
        res = self.backup_file(path)
        if isinstance(res, dict) and "backup" in res:
            return Path(res["backup"])
        return None

    def backup_file(self, path: str | Path) -> dict[str, Any]:
        """Create a timestamped backup with SHA-256 hash."""
        src = Path(path).expanduser().resolve()
        if not src.is_file():
            return {}
        try:
            data = src.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            dest = self.backup_dir / f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}-{src.name}"
            shutil.copy2(src, dest)
            return {"backup": str(dest), "sha256": digest, "original": str(src)}
        except OSError:
            return {}

    def history(self, limit: int = 20, case_id: str | None = None) -> list[dict[str, Any]]:
        """Read recent journal records from disk."""
        if not self.path or not self.path.exists():
            return [asdict(e) for e in self.entries[-limit:]]
        rows = []
        try:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if case_id is None or row.get("case_id") == case_id:
                        rows.append(row)
                except json.JSONDecodeError:
                    continue
        except OSError:
            pass
        return rows[-limit:] if rows else [asdict(e) for e in self.entries[-limit:]]

    def find(self, entry_id: str) -> dict[str, Any] | None:
        for row in reversed(self.history(limit=100000)):
            if row.get("id") == entry_id or row.get("action_id") == entry_id:
                return row
        return None

    def latest_reversible(self) -> dict[str, Any] | None:
        for row in reversed(self.history(limit=100000)):
            if row.get("backups") or row.get("reversibility") == Reversibility.FULL.value:
                return row
        return None

    def _write(self, record: dict[str, Any]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
