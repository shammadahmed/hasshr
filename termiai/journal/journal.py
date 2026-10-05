import json
import re
from pathlib import Path

from .models import Action

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret[_-]?key|token|password|secret)\s*[=:]\s*([^\s,;]+)"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
]


def redact(value):
    if value is None:
        return None
    text = str(value)
    for p in _SECRET_PATTERNS:
        text = p.sub("[REDACTED]", text)
    return text


class Journal:
    """Append-only JSONL action journal. Updates are new events; history is never rewritten."""

    def __init__(self, base_dir=".termiai"):
        self.base_dir = Path(base_dir)
        self.journal_dir = self.base_dir / "journal"
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.journal_dir / "actions.jsonl"

    def start_action(self, user_request, operation, target, **kwargs):
        action = Action.create(redact(user_request), operation, target, **kwargs)
        self.append(action)
        return action

    def append(self, action):
        data = action.to_dict()
        data["output"] = redact(data.get("output"))
        data["verification_details"] = redact(data.get("verification_details"))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + "\n")
            f.flush()

    def update(self, action, **changes):
        data = action.to_dict()
        data.update(changes)
        data["output"] = redact(data.get("output"))
        data["verification_details"] = redact(data.get("verification_details"))
        action = Action(**data)
        self.append(action)
        return action

    def history(self, limit=20, case_id=None):
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # corrupted tail/event stays observable on disk but does not break history
            if case_id is None or row.get("case_id") == case_id:
                rows.append(row)
        return rows[-limit:]

    def find(self, action_id):
        matches = [x for x in self.history(limit=100000) if x.get("action_id") == action_id]
        return matches[-1] if matches else None

    def latest_reversible(self):
        seen = set()
        for row in reversed(self.history(limit=100000)):
            aid = row.get("action_id")
            if aid in seen:
                continue
            seen.add(aid)
            latest = self.find(aid)
            if latest and latest.get("undo_available") and latest.get("snapshot_id"):
                return latest
        return None
