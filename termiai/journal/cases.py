import json
from datetime import datetime, timezone
from pathlib import Path


class CaseJournal:
    """Small persistence helper for M5 integration; M5 remains owner of case orchestration."""
    def __init__(self, base_dir=".termiai"):
        self.root = Path(base_dir) / "cases"
        self.root.mkdir(parents=True, exist_ok=True)

    def record_attempt(self, case_id, problem, attempt, hypothesis, test, result,
                       evidence=None, confidence="LOW", next_test=None, status="INVESTIGATING"):
        path = self.root / f"{case_id}.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {
            "case_id": case_id, "problem": problem, "status": status, "attempts": []
        }
        data["status"] = status
        data["attempts"].append({
            "attempt": attempt, "timestamp": datetime.now(timezone.utc).isoformat(),
            "hypothesis": hypothesis, "test": test, "result": result,
            "evidence": evidence, "confidence": confidence, "next_test": next_test,
        })
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data
