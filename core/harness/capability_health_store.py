"""Durable, content-free capability health aggregation."""

from __future__ import annotations

import math
import sqlite3
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from core.coding.run_coordinator import RunEvent

_FAILURE_CATEGORIES = frozenset(
    {
        "approval_denied",
        "execution_error",
        "policy_blocked",
        "timeout",
        "tool_error",
        "unavailable",
    }
)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_invocations (
    event_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    catalog_revision TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('success', 'failure')),
    duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
    failure_category TEXT
);
CREATE INDEX IF NOT EXISTS capability_invocations_scope_time_idx
ON capability_invocations(owner_id, workspace_id, occurred_at);
CREATE INDEX IF NOT EXISTS capability_invocations_capability_time_idx
ON capability_invocations(capability_id, occurred_at);
"""


class CapabilityHealthStore:
    """SQLite projection keyed by durable timeline event IDs."""

    def __init__(self, path: str | Path) -> None:
        expanded = Path(path).expanduser()
        self.path = expanded if expanded.is_absolute() else Path.cwd() / expanded
        self._initialized = False
        self._initialize_lock = Lock()
        self._validate_path()

    def record_event(
        self,
        event: RunEvent,
        *,
        owner_id: str,
        workspace_id: str,
        session_id: str,
        run_id: str,
        occurred_at: datetime | None = None,
    ) -> bool:
        """Project one validated invocation event without persisting tool content."""
        if event.payload.get("type") != "capability_invocation_completed":
            return False
        event_id = _bounded_id(event.event_id, "event_id", limit=512)
        capability_id = _bounded_id(
            event.payload.get("capability_id"), "capability_id", limit=256
        )
        catalog_revision = _bounded_id(
            event.payload.get("catalog_revision"), "catalog_revision"
        )
        status = str(event.payload.get("status", ""))
        if status not in {"success", "failure"}:
            raise ValueError("capability status must be success or failure")
        duration_ms = event.payload.get("duration_ms")
        if type(duration_ms) is not int or duration_ms < 0:
            raise ValueError("capability duration must be a non-negative integer")
        failure_category: str | None = None
        if status == "failure":
            candidate = str(event.payload.get("failure_category", ""))
            failure_category = (
                candidate if candidate in _FAILURE_CATEGORIES else "execution_error"
            )
        event_time = occurred_at or _parse_event_timestamp(event.timestamp) or datetime.now(UTC)
        timestamp = event_time.astimezone(UTC).isoformat()
        self._ensure_ready()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO capability_invocations (
                    event_id, owner_id, workspace_id, session_id, run_id,
                    capability_id, catalog_revision, occurred_at, status,
                    duration_ms, failure_category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    _bounded_id(owner_id, "owner_id", limit=256),
                    _bounded_id(workspace_id, "workspace_id", limit=256),
                    _bounded_id(session_id, "session_id"),
                    _bounded_id(run_id, "run_id"),
                    capability_id,
                    catalog_revision,
                    timestamp,
                    status,
                    duration_ms,
                    failure_category,
                ),
            )
            connection.commit()
            return cursor.rowcount == 1

    def summary(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        days: int,
        now: datetime | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Aggregate one owner/workspace scope with nearest-rank percentiles."""
        if days <= 0 or days > 3650:
            raise ValueError("capability health range must be between 1 and 3650 days")
        self._validate_path()
        if not self.path.exists():
            return {}
        self._ensure_ready()
        reference = (now or datetime.now(UTC)).astimezone(UTC)
        cutoff = (reference - timedelta(days=days)).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM capability_invocations
                WHERE owner_id = ? AND workspace_id = ? AND occurred_at >= ?
                ORDER BY capability_id, occurred_at, event_id
                """,
                (
                    _bounded_id(owner_id, "owner_id", limit=256),
                    _bounded_id(workspace_id, "workspace_id", limit=256),
                    cutoff,
                ),
            ).fetchall()
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(str(row["capability_id"]), []).append(row)
        return {
            capability_id: _summarize_rows(values)
            for capability_id, values in grouped.items()
        }

    def _validate_path(self) -> None:
        if self.path.is_symlink():
            raise ValueError("capability health database must not be a symlink")
        if self.path.parent.exists() and self.path.parent.is_symlink():
            raise ValueError("capability health database directory must not be a symlink")

    def _ensure_ready(self) -> None:
        with self._initialize_lock:
            if self._initialized:
                return
            self._validate_path()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._validate_path()
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
                connection.commit()
            self._initialized = True

    def _connect(self) -> sqlite3.Connection:
        self._validate_path()
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection


def _summarize_rows(rows: list[sqlite3.Row]) -> dict[str, Any]:
    successes = [row for row in rows if row["status"] == "success"]
    failures = [row for row in rows if row["status"] == "failure"]
    durations = sorted(int(row["duration_ms"]) for row in rows)
    failure_categories = Counter(
        str(row["failure_category"])
        for row in failures
        if row["failure_category"] is not None
    )
    return {
        "invocation_count": len(rows),
        "success_count": len(successes),
        "failure_count": len(failures),
        "first_success_at": str(successes[0]["occurred_at"]) if successes else None,
        "last_success_at": str(successes[-1]["occurred_at"]) if successes else None,
        "p50_latency_ms": _nearest_rank(durations, 0.50),
        "p95_latency_ms": _nearest_rank(durations, 0.95),
        "failure_categories": dict(sorted(failure_categories.items())),
    }


def _nearest_rank(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    index = max(0, math.ceil(percentile * len(values)) - 1)
    return values[index]


def _parse_event_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("capability event timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _bounded_id(value: object, field: str, *, limit: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"{field} must be between 1 and {limit} characters")
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} contains control characters")
    return value


__all__ = ["CapabilityHealthStore"]
