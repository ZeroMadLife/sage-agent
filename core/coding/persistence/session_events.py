"""Durable session event bus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.coding.context import now


class SessionEventBus:
    """Append coarse-grained session events to JSONL."""

    def __init__(self, session_id: str, path: Path) -> None:
        self.session_id = session_id
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Append one event and return the stored record."""
        record = dict(payload or {})
        record["event"] = event
        record["session_id"] = self.session_id
        record["created_at"] = now()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return record
