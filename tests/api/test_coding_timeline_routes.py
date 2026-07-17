from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.coding import _timeline_status
from api.main import create_app
from tests.api.test_coding_routes import FakeModel


def _receive_until_terminal(websocket) -> list[dict]:
    events: list[dict] = []
    while True:
        event = websocket.receive_json()
        events.append(event)
        if event["kind"] == "terminal":
            return events


def _app(tmp_path: Path):
    return create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [("cancelled", "cancelled"), ("interrupted", "interrupted"), ("retryable", "interrupted")],
)
def test_run_finished_timeline_status_preserves_valid_terminal_status(
    status: str, expected: str,
) -> None:
    assert _timeline_status("run_finished", {"status": status}) == expected


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
        "older_cursor": None,
        "latest_cursor": 0,
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


def test_timeline_tail_and_before_keep_history_bounded(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        coordinator = app.state.coding_run_registry.get(session_id)
        for index in range(205):
            coordinator.journal.append(
                run_id=f"run-{index}",
                kind="system",
                status="completed",
                payload={"index": index},
            )

        tail = client.get(
            f"/api/v1/coding/session/{session_id}/timeline?tail=true&limit=100"
        ).json()
        older = client.get(
            f"/api/v1/coding/session/{session_id}/timeline"
            f"?before={tail['older_cursor']}&limit=100"
        ).json()

    assert [item["sequence"] for item in tail["items"]] == list(range(106, 206))
    assert tail["older_cursor"] == 106
    assert tail["latest_cursor"] == 205
    assert tail["next_cursor"] == 205
    assert tail["has_more"] is True
    assert [item["sequence"] for item in older["items"]] == list(range(6, 106))
    assert older["older_cursor"] == 6
    assert older["latest_cursor"] == 205


def test_timeline_rejects_mixed_forward_and_backward_cursors(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        response = client.get(
            f"/api/v1/coding/session/{session_id}/timeline?after=2&before=3"
        )

    assert response.status_code == 422


def test_websocket_streams_envelopes_and_rest_replays_same_event_ids(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("timeline\n", encoding="utf-8")
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        with client.websocket_connect(
            f"/api/v1/coding/{session_id}/stream?after=0"
        ) as websocket:
            websocket.send_json({"content": "read"})
            streamed = _receive_until_terminal(websocket)
        replayed = client.get(
            f"/api/v1/coding/session/{session_id}/timeline?limit=100"
        ).json()["items"]

    assert [item["event_id"] for item in streamed] == [
        item["event_id"] for item in replayed
    ]
    assert [item["sequence"] for item in streamed] == list(
        range(1, len(streamed) + 1)
    )
    assert sum(item["kind"] == "terminal" for item in streamed) == 1
    assert streamed[-1]["payload"] == {"event": "run_completed"}
    assert any(item["kind"] == "user" for item in streamed)
    runtime_events = [
        item for item in streamed if item["payload"].get("type") == "run_finished"
    ]
    assert len(runtime_events) == 1
    assert runtime_events[0]["kind"] == "run"
    stage_events = [
        item
        for item in streamed
        if item["kind"] == "harness"
        and item["payload"].get("type")
        in {"stage_started", "stage_completed", "transition_taken"}
    ]
    assert stage_events
    assert {item["payload"].get("stage_id") for item in stage_events} >= {
        "receive",
        "context",
        "plan",
        "act",
        "reply",
    }
    retrieval_decisions = [
        item
        for item in streamed
        if item["payload"].get("type") == "retrieval_decision"
    ]
    assert [item["payload"]["decision"] for item in retrieval_decisions] == ["skip"]


def test_runtime_error_becomes_error_terminal_after_turn_stream_exhausts(
    tmp_path: Path,
) -> None:
    class ExplodingModel:
        async def complete(self, prompt: str) -> str:
            del prompt
            raise RuntimeError("secret provider failure")

    app = create_app(
        coding_model_factory=ExplodingModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "fail"})
            events = _receive_until_terminal(websocket)

    assert events[-1]["status"] == "error"
    assert events[-1]["payload"] == {"event": "run_error"}
    assert any(item["payload"].get("type") == "turn_finished" for item in events[:-1])
    assert any(
        item["kind"] == "harness"
        and item["payload"].get("type") == "stage_failed"
        and item["payload"].get("stage_id") == "plan"
        for item in events[:-1]
    )
    assert any(
        item["payload"].get("type") == "retrieval_decision"
        and item["payload"].get("decision") == "skip"
        for item in events[:-1]
    )


def test_runtime_busy_without_run_finished_becomes_error_terminal(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        runtime = app.state.coding_sessions[session_id]
        runtime.active_run_id = "runtime-owned-elsewhere"
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "busy"})
            events = _receive_until_terminal(websocket)

    assert any(item["payload"].get("type") == "error" for item in events)
    assert events[-1]["status"] == "error"
    assert events[-1]["payload"] == {"event": "run_error"}


def test_rejected_input_run_ends_with_error_terminal(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "/not-installed"})
            events = _receive_until_terminal(websocket)

    error = next(item for item in events if item["payload"].get("type") == "error")
    assert "Unknown skill" in error["payload"]["message"]
    assert events[-1]["status"] == "error"
    assert events[-1]["payload"] == {"event": "input_rejected"}


def test_websocket_after_cursor_replays_only_missing_events(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("timeline\n", encoding="utf-8")
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "read"})
            first_run = _receive_until_terminal(websocket)
        cursor = first_run[2]["sequence"]
        with client.websocket_connect(
            f"/api/v1/coding/{session_id}/stream?after={cursor}"
        ) as websocket:
            missing = [websocket.receive_json() for _ in range(len(first_run) - 3)]

    assert [item["sequence"] for item in missing] == [
        item["sequence"] for item in first_run[3:]
    ]


def test_disconnect_does_not_cancel_approval_blocked_run_and_rest_can_resume(
    tmp_path: Path,
) -> None:
    from tests.api.test_coding_routes import FakeWriteModel

    app = create_app(
        coding_model_factory=FakeWriteModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session", json={"approval_policy": "ask"}
        ).json()["session_id"]
        cursor = 0
        approval_id = ""
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "write"})
            while not approval_id:
                event = websocket.receive_json()
                cursor = event["sequence"]
                if event["payload"].get("type") == "approval_required":
                    approval_id = event["payload"]["approval_id"]

        active = client.get(
            f"/api/v1/coding/session/{session_id}/timeline"
        ).json()["active_run"]
        assert active is not None
        blocked_events = client.get(
            f"/api/v1/coding/session/{session_id}/timeline?limit=100"
        ).json()["items"]
        assert any(
            item["kind"] == "harness"
            and item["status"] == "blocked"
            and item["payload"].get("type") == "stage_started"
            and item["payload"].get("stage_id") == "act"
            and item["payload"].get("detail") == "write_file · note.txt"
            for item in blocked_events
        )
        response = client.post(
            f"/api/v1/coding/{session_id}/approval/respond",
            json={"approval_id": approval_id, "choice": "once"},
        )
        assert response.status_code == 200
        with client.websocket_connect(
            f"/api/v1/coding/{session_id}/stream?after={cursor}"
        ) as websocket:
            resumed = _receive_until_terminal(websocket)

    assert resumed[-1]["kind"] == "terminal"
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "approved"


def test_two_subscribers_receive_same_events_without_duplicates(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("timeline\n", encoding="utf-8")
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        received: list[list[dict]] = [[], []]
        ready = Event()

        def listen(index: int, start: bool) -> None:
            with client.websocket_connect(
                f"/api/v1/coding/{session_id}/stream?after=0"
            ) as websocket:
                if start:
                    ready.wait(timeout=2)
                    websocket.send_json({"content": "read"})
                else:
                    ready.set()
                received[index] = _receive_until_terminal(websocket)

        threads = [Thread(target=listen, args=(0, True)), Thread(target=listen, args=(1, False))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert [item["event_id"] for item in received[0]] == [
        item["event_id"] for item in received[1]
    ]
    assert len({item["event_id"] for item in received[0]}) == len(received[0])


def test_second_input_while_active_is_persisted_as_rejected_run(tmp_path: Path) -> None:
    from tests.api.test_coding_routes import FakeWriteModel

    app = create_app(
        coding_model_factory=FakeWriteModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session", json={"approval_policy": "ask"}
        ).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as first:
            first.send_json({"content": "write"})
            approval_id = ""
            cursor = 0
            original_run_id = ""
            while not approval_id:
                event = first.receive_json()
                cursor = event["sequence"]
                original_run_id = event["run_id"]
                if event["payload"].get("type") == "approval_required":
                    approval_id = event["payload"]["approval_id"]
            with client.websocket_connect(
                f"/api/v1/coding/{session_id}/stream?after={cursor}"
            ) as second:
                second.send_json({"content": "second input"})
                rejected = _receive_until_terminal(second)
            assert app.state.coding_run_registry.get(session_id).active_run_id == original_run_id
            client.post(
                f"/api/v1/coding/{session_id}/approval/respond",
                json={"approval_id": approval_id, "choice": "once"},
            )
            while first.receive_json()["kind"] != "terminal":
                pass

        replay = client.get(
            f"/api/v1/coding/session/{session_id}/timeline?limit=100"
        ).json()["items"]

    rejected_run_id = rejected[-1]["run_id"]
    assert rejected_run_id != original_run_id
    assert rejected[-1]["status"] == "error"
    assert any(
        item["run_id"] == rejected_run_id
        and item["payload"].get("content") == "second input"
        for item in replay
    )


def test_sessions_are_isolated(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("timeline\n", encoding="utf-8")
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_a = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        session_b = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_a}/stream") as websocket:
            websocket.send_json({"content": "only-a"})
            _receive_until_terminal(websocket)
        timeline_b = client.get(
            f"/api/v1/coding/session/{session_b}/timeline"
        ).json()["items"]

    assert timeline_b == []


def test_stop_cancels_only_matching_coordinator_run(tmp_path: Path) -> None:
    from tests.api.test_coding_routes import FakeWriteModel

    app = create_app(
        coding_model_factory=FakeWriteModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session", json={"approval_policy": "ask"}
        ).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "write"})
            run_id = ""
            while not run_id:
                event = websocket.receive_json()
                if event["payload"].get("type") == "approval_required":
                    run_id = event["run_id"]
            stale = client.post(
                f"/api/v1/coding/{session_id}/run/stop", json={"run_id": "old-run"}
            )
            assert stale.json() == {"ok": False}
            assert app.state.coding_run_registry.get(session_id).active_run_id == run_id
            matching = client.post(
                f"/api/v1/coding/{session_id}/run/stop", json={"run_id": run_id}
            )
            assert matching.json() == {"ok": True}
            terminal = websocket.receive_json()
            while terminal["kind"] != "terminal":
                terminal = websocket.receive_json()

    assert terminal["status"] == "cancelled"
    assert terminal["run_id"] == run_id
    assert app.state.coding_sessions[session_id].active_run_id is None


def test_compact_and_model_switch_reject_persistent_active_lease(tmp_path: Path) -> None:
    from tests.api.test_coding_context_routes import _app as context_app

    app = context_app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        coordinator = app.state.coding_run_registry.get(session_id)
        begin = coordinator.journal.begin_run(
            "run-busy", owner_id=coordinator.owner_id, owner_pid=coordinator.owner_pid
        )
        compact = client.post(
            f"/api/v1/coding/{session_id}/context/compact", json={"focus": "x"}
        )
        switch = client.patch(
            f"/api/v1/coding/{session_id}/model",
            json={"model_id": "model-b"},
        )
        coordinator.journal.append_terminal_and_release(
            run_id="run-busy",
            status="cancelled",
            payload={"event": "test_cleanup"},
            lease_owner_id=coordinator.owner_id,
            fencing_token=begin.fencing_token,
        )

    assert compact.status_code == 409
    assert switch.status_code == 409


def test_new_app_marks_abandoned_run_interrupted_once_without_resuming(
    tmp_path: Path,
) -> None:
    first_app = _app(tmp_path)
    with TestClient(first_app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        coordinator = first_app.state.coding_run_registry.get(session_id)
        coordinator.journal.begin_run(
            "run-abandoned",
            owner_id=coordinator.owner_id,
            owner_pid=coordinator.owner_pid,
        )
        with sqlite3.connect(coordinator.journal.path) as connection:
            connection.execute(
                "UPDATE active_run_lease SET owner_process_start = ?",
                ("definitely-not-this-process",),
            )

    restarted = _app(tmp_path)
    with TestClient(restarted) as client:
        first = client.get(
            f"/api/v1/coding/session/{session_id}/timeline"
        ).json()
        second = client.get(
            f"/api/v1/coding/session/{session_id}/timeline"
        ).json()

    terminal = [item for item in first["items"] if item["kind"] == "terminal"]
    assert len(terminal) == 1
    assert terminal[0]["status"] == "interrupted"
    assert terminal[0]["payload"] == {
        "event": "run_interrupted",
        "retryable": True,
    }
    assert first["active_run"] is None
    assert second["items"] == first["items"]
    assert restarted.state.coding_run_registry.get(session_id).active_run_id is None


def test_app_shutdown_cancels_blocked_run_once_and_releases_lease(tmp_path: Path) -> None:
    from tests.api.test_coding_routes import FakeWriteModel

    app = create_app(
        coding_model_factory=FakeWriteModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    with TestClient(app) as client:
        session_id = client.post(
            "/api/v1/coding/session", json={"approval_policy": "ask"}
        ).json()["session_id"]
        with client.websocket_connect(f"/api/v1/coding/{session_id}/stream") as websocket:
            websocket.send_json({"content": "write"})
            while websocket.receive_json()["payload"].get("type") != "approval_required":
                pass
        journal = app.state.coding_run_registry.get(session_id).journal
        assert journal.active_run_id() is not None

    terminal = [
        item for item in journal.replay(after=0, limit=100).items if item.kind == "terminal"
    ]
    assert len(terminal) == 1
    assert terminal[0].status == "cancelled"
    assert journal.active_run_id() is None


def test_malicious_session_id_is_rejected_without_creating_evidence(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/v1/coding/session/%2E%2E%3Aescape/timeline")

    assert response.status_code in {404, 422}
    assert not (tmp_path / ".coding" / "evidence" / "..:escape").exists()


def test_websocket_malicious_session_id_closes_with_policy_violation(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect) as raised,
        client.websocket_connect(
            "/api/v1/coding/%2E%2E%3Aescape/stream"
        ) as websocket,
    ):
        websocket.receive_json()

    assert raised.value.code == 1008
    assert not (tmp_path / ".coding" / "evidence" / "..:escape").exists()
