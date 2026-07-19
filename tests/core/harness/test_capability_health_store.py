"""Content-free capability health persistence tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import pytest

from core.coding.run_coordinator import RunEvent
from core.harness.capability_health_store import CapabilityHealthStore


class _RecordScope(TypedDict):
    owner_id: str
    workspace_id: str
    session_id: str
    run_id: str
    occurred_at: datetime


def _event(
    event_id: str,
    *,
    status: str = "success",
    duration_ms: int = 10,
    failure_category: str | None = None,
    timestamp: str | None = None,
) -> RunEvent:
    payload: dict[str, object] = {
        "type": "capability_invocation_completed",
        "version": 1,
        "capability_id": "local:list_files",
        "catalog_revision": "catalog-r1",
        "status": status,
        "duration_ms": duration_ms,
    }
    if failure_category is not None:
        payload["failure_category"] = failure_category
    return RunEvent(
        kind="harness",
        status="completed",
        payload=payload,
        event_id=event_id,
        timestamp=timestamp,
    )


def test_store_is_idempotent_scoped_and_aggregates_percentiles(tmp_path: Path) -> None:
    store = CapabilityHealthStore(tmp_path / ".coding" / "capability-health.sqlite3")
    occurred_at = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
    scope: _RecordScope = {
        "owner_id": "owner-a",
        "workspace_id": "workspace-a",
        "session_id": "session-a",
        "run_id": "run-a",
        "occurred_at": occurred_at,
    }

    assert store.record_event(_event("event-1", duration_ms=10), **scope)
    assert not store.record_event(_event("event-1", duration_ms=10), **scope)
    assert store.record_event(_event("event-2", duration_ms=20), **scope)
    assert store.record_event(
        _event(
            "event-3",
            status="failure",
            duration_ms=100,
            failure_category="timeout",
        ),
        **scope,
    )
    assert store.record_event(
        _event("event-other", duration_ms=999),
        owner_id="owner-b",
        workspace_id=scope["workspace_id"],
        session_id=scope["session_id"],
        run_id=scope["run_id"],
        occurred_at=scope["occurred_at"],
    )

    summary = store.summary(
        owner_id="owner-a",
        workspace_id="workspace-a",
        days=30,
        now=datetime(2026, 7, 18, 12, tzinfo=UTC),
    )["local:list_files"]

    assert summary == {
        "invocation_count": 3,
        "success_count": 2,
        "failure_count": 1,
        "first_success_at": occurred_at.isoformat(),
        "last_success_at": occurred_at.isoformat(),
        "p50_latency_ms": 20,
        "p95_latency_ms": 100,
        "failure_categories": {"timeout": 1},
    }


def test_store_ignores_non_invocation_and_rejects_symlink(tmp_path: Path) -> None:
    store = CapabilityHealthStore(tmp_path / "health.sqlite3")
    assert not store.record_event(
        RunEvent(
            kind="harness",
            status="completed",
            payload={"type": "capability_selected"},
            event_id="selection-1",
        ),
        owner_id="owner",
        workspace_id="workspace",
        session_id="session",
        run_id="run",
    )

    target = tmp_path / "target.sqlite3"
    target.touch()
    linked = tmp_path / "linked.sqlite3"
    linked.symlink_to(target)
    with pytest.raises(ValueError, match="must not be a symlink"):
        CapabilityHealthStore(linked)


def test_store_uses_durable_event_timestamp(tmp_path: Path) -> None:
    store = CapabilityHealthStore(tmp_path / "health.sqlite3")
    timestamp = "2026-07-01T08:30:00+00:00"
    assert store.record_event(
        _event("event-timestamp", timestamp=timestamp),
        owner_id="owner",
        workspace_id="workspace",
        session_id="session",
        run_id="run",
    )

    summary = store.summary(
        owner_id="owner",
        workspace_id="workspace",
        days=30,
        now=datetime(2026, 7, 18, tzinfo=UTC),
    )["local:list_files"]
    assert summary["first_success_at"] == timestamp
    assert summary["last_success_at"] == timestamp
