from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app
from tests.api.test_coding_routes import FakeModel


def _app(tmp_path: Path):
    return create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )


def test_timeline_missing_session_is_404_without_creating_database(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/v1/coding/session/missing/timeline")

    assert response.status_code == 404
    assert not (tmp_path / ".coding" / "evidence" / "missing").exists()


def test_timeline_empty_page_and_query_validation(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        response = client.get(f"/api/v1/coding/session/{session_id}/timeline")
        invalid_after = client.get(
            f"/api/v1/coding/session/{session_id}/timeline?after=-1"
        )
        invalid_limit = client.get(
            f"/api/v1/coding/session/{session_id}/timeline?limit=501"
        )

    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "next_cursor": 0,
        "has_more": False,
        "active_run": None,
    }
    assert invalid_after.status_code == 422
    assert invalid_limit.status_code == 422


def test_timeline_paginates_envelopes_and_reports_active_run(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        coordinator = app.state.coding_run_registry.get(session_id)
        begin = coordinator.journal.begin_run(
            "run-active",
            owner_id=coordinator.owner_id,
            owner_pid=coordinator.owner_pid,
        )
        coordinator.journal.append(
            run_id="run-active",
            kind="user",
            status="completed",
            payload={"content": "hi"},
            lease_owner_id=coordinator.owner_id,
            fencing_token=begin.fencing_token,
        )
        first = client.get(
            f"/api/v1/coding/session/{session_id}/timeline?after=0&limit=1"
        )
        coordinator.journal.append_terminal_and_release(
            run_id="run-active",
            status="cancelled",
            payload={"event": "test_cleanup"},
            lease_owner_id=coordinator.owner_id,
            fencing_token=begin.fencing_token,
        )

    body = first.json()
    assert first.status_code == 200
    assert body["has_more"] is True
    assert body["next_cursor"] == body["items"][0]["sequence"]
    assert body["active_run"] == {"run_id": "run-active", "status": "running"}
    assert set(body["items"][0]) == {
        "event_id",
        "session_id",
        "run_id",
        "sequence",
        "kind",
        "status",
        "timestamp",
        "payload",
    }
