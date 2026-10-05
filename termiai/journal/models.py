import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Action:
    action_id: str
    timestamp: str
    user_request: str
    operation: str
    target: str
    session_id: Optional[str] = None
    case_id: Optional[str] = None
    step: Optional[int] = None
    command: Optional[str] = None
    risk_level: Optional[str] = None
    approval_status: Optional[str] = None
    reversibility: str = "None"
    status: str = "PENDING"
    output: Optional[str] = None
    exit_code: Optional[int] = None
    verification_status: str = "NOT_RUN"
    verification_details: Optional[str] = None
    snapshot_id: Optional[str] = None
    undo_available: bool = False
    undo_status: Optional[str] = None
    result_summary: Optional[str] = None

    @classmethod
    def create(cls, user_request, operation, target, **kwargs):
        return cls(
            action_id="ACT-" + uuid.uuid4().hex[:8].upper(),
            timestamp=utc_now(),
            user_request=user_request,
            operation=operation,
            target=target,
            **kwargs,
        )

    def to_dict(self):
        return asdict(self)
