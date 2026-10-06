import hashlib
from pathlib import Path


class Verifier:
    @staticmethod
    def sha256(path):
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()

    def verify_exists(self, path):
        p = Path(path).expanduser()
        return p.exists(), f"exists={p.exists()}"

    def verify_copy(self, source, destination):
        s, d = Path(source).expanduser(), Path(destination).expanduser()
        if not d.is_file():
            return False, "destination_missing"
        if not s.is_file():
            return False, "source_missing"
        if s.stat().st_size != d.stat().st_size:
            return False, "size_mismatch"
        if self.sha256(s) != self.sha256(d):
            return False, "sha256_mismatch"
        return True, "destination_exists_size_and_sha256_match"

    def verify_deleted(self, path):
        p = Path(path).expanduser()
        return (not p.exists()), f"exists={p.exists()}"

    def verify_content(self, path, expected):
        p = Path(path).expanduser()
        if not p.is_file():
            return False, "file_missing"
        ok = p.read_text(encoding="utf-8") == expected
        return ok, "content_matches" if ok else "content_mismatch"

    def verify_restored(self, path, expected_sha256):
        p = Path(path).expanduser()
        if not p.is_file():
            return False, "restored_file_missing"
        ok = self.sha256(p) == expected_sha256
        return ok, "sha256_matches_snapshot" if ok else "sha256_mismatch"
