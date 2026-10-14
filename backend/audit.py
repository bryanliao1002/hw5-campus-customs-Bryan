"""Append-only, process-safe JSON-array audit writer shared with MCP operations."""

from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = Path(os.environ.get(
    "CAMPUS_CUSTOMS_AUDIT_PATH", str(PROJECT_ROOT / "output" / "audit_trail.json")
)).expanduser().resolve()


def append_audit_record(record: dict[str, Any]) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = AUDIT_PATH.with_suffix(AUDIT_PATH.suffix + ".lock")
    item = {"timestamp": datetime.now(timezone.utc).isoformat(), **record}
    encoded = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        if not AUDIT_PATH.exists() or AUDIT_PATH.stat().st_size == 0:
            AUDIT_PATH.write_text("[]\n", encoding="utf-8")
        with AUDIT_PATH.open("r+b") as audit_file:
            content = audit_file.read()
            stripped = content.rstrip()
            if not stripped.endswith(b"]"):
                raise ValueError("Audit trail is not a JSON array; refusing to overwrite it")
            prefix = stripped[:-1].rstrip()
            separator = b"" if prefix == b"[" else b"," 
            audit_file.seek(len(prefix))
            audit_file.write(separator + encoded.encode("utf-8") + b"]\n")
            audit_file.truncate()
            audit_file.flush()
            os.fsync(audit_file.fileno())
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def read_audit_records(limit: int = 100) -> list[dict[str, Any]]:
    """Read a consistent tail of the append-only audit log for dashboard polling."""
    if limit < 1:
        raise ValueError("limit must be positive")
    lock_path = AUDIT_PATH.with_suffix(AUDIT_PATH.suffix + ".lock")
    with lock_path.open("a+b") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
        if not AUDIT_PATH.exists():
            records: list[dict[str, Any]] = []
        else:
            parsed = json.loads(AUDIT_PATH.read_text(encoding="utf-8"))
            if not isinstance(parsed, list):
                raise ValueError("Audit trail must be a JSON array")
            records = parsed[-limit:]
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    return records
