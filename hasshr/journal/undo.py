from .journal import Journal
from .snapshot import SnapshotManager


class UndoManager:
    def __init__(self, base_dir=".hasshr"):
        self.journal = Journal(base_dir)
        self.snapshots = SnapshotManager(base_dir)

    def undo(self, action_id=None, overwrite=False):
        action = self.journal.find(action_id) if action_id else self.journal.latest_reversible()
        if not action:
            raise ValueError("No reversible action found")
        snapshot_id = action.get("snapshot_id")
        if not snapshot_id or not action.get("undo_available"):
            raise ValueError("Action is not currently reversible")
        manifest = self.snapshots.restore(snapshot_id, overwrite=overwrite)
        restore = self.journal.start_action(
            user_request=f"/undo {action['action_id']}", operation="RESTORE",
            target=manifest["original_path"], reversibility="None",
            case_id=action.get("case_id"),
        )
        return self.journal.update(
            restore, status="SUCCESS", verification_status="PASSED",
            verification_details="SHA-256 verified after restore",
            snapshot_id=snapshot_id, undo_available=False,
            result_summary=f"Restored {action['action_id']} from {snapshot_id}",
        )
