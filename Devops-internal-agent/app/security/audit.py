"""Append-only compliance audit logger for all DevOps AI Agent interactions."""

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List


class AuditLogger:
    """Writes immutable JSONL audit records for security compliance review."""

    def __init__(self, log_path: str) -> None:
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def log_event(
        self,
        event_type: str,
        user_email: str,
        role: str,
        source: str,
        details: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Append a structured audit event to the JSONL log."""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "user_email": user_email,
            "role": role,
            "source": source,
            "details": details,
        }
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        return record

    def read_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent audit records in reverse chronological order."""
        if not self.log_path.exists():
            return []
        with self._lock:
            lines = self.log_path.read_text(encoding="utf-8").splitlines()
        recent = lines[-max(1, limit) :]
        records: List[Dict[str, Any]] = []
        for raw in reversed(recent):
            if raw.strip():
                try:
                    records.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        return records
