import hashlib
import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path


class SnapshotManager:
    def __init__(self, base_dir=".termiai"):
        self.base_dir = Path(base_dir)
        self.root = self.base_dir / "snapshots"
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sha256(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def snapshot_file(self, source, action_id=None):
        source = Path(source).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        snapshot_id = "SNAP-" + uuid.uuid4().hex[:8].upper()
        folder = self.root / snapshot_id
        folder.mkdir(parents=True)
        backup = folder / source.name
        shutil.copy2(source, backup)
        manifest = {
            "snapshot_id": snapshot_id,
            "action_id": action_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "original_path": str(source),
            "backup_path": str(backup),
            "sha256": self.sha256(source),
            "size": source.stat().st_size,
            "mtime": source.stat().st_mtime,
            "restore_status": "NOT_RUN",
        }
        (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest

    def restore(self, snapshot_id, destination=None, overwrite=False):
        folder = self.root / snapshot_id
        manifest_path = folder / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Snapshot not found: {snapshot_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        backup = Path(manifest["backup_path"])
        target = Path(destination or manifest["original_path"]).expanduser()
        if target.exists() and not overwrite:
            raise FileExistsError(f"Refusing to overwrite existing target without approval: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, target)
        if self.sha256(target) != manifest["sha256"]:
            raise RuntimeError("Restore verification failed: SHA-256 mismatch")
        manifest["restore_status"] = "VERIFIED"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return manifest
