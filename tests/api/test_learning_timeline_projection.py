from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.main import create_app
from core.coding.persistence import CodingSessionStore
from core.coding.runtime import CodingRuntime

SOURCE_PATH_SENTINEL = "/private/source-path-sentinel"
GOAL_SENTINEL = "PRIVATE_GOAL_SENTINEL"
CRITERION_SENTINEL = "PRIVATE_CRITERION_SENTINEL"
MODEL_TEXT_SENTINEL = "PRIVATE_MODEL_NEXT_ACTION_SENTINEL"


class _FakeModel:
    async def complete(self, prompt: str) -> str:
        del prompt
        return "<final>ok</final>"


def _app(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return create_app(
        coding_model_factory=_FakeModel,
        coding_workspace_root=workspace,
        coding_storage_root=tmp_path / ".coding",
        coding_default_runtime_profile="legacy",
    )


def _activate_learning(client: TestClient) -> tuple[dict, dict]:
    draft_response = client.post(
        "/api/v1/learning/tasks/draft",
        json={
            "topic": "学习公共 Timeline 投影",
            "desired_outcome": "能复核浏览器边界不泄露内部学习上下文",
            "starting_level": "beginner",
            "time_budget_minutes_per_week": 120,
        },
    )
    assert draft_response.status_code == 201
    task = draft_response.json()
    activation_response = client.post(
        f"/api/v1/learning/tasks/{task['task_id']}/activate",
        headers={"Idempotency-Key": "learning-timeline-projection-v1"},
        json={"expected_revision": task["task_revision"]},
    )
    assert activation_response.status_code == 200, activation_response.json()
    return task, activation_response.json()


def _install_runtime(app, tmp_path: Path, session_id: str) -> None:
    storage_root = tmp_path / ".coding"
    session = CodingSessionStore(storage_root / "sessions").load(session_id)
    app.state.coding_sessions[session_id] = CodingRuntime(
        session_id=session_id,
        workspace_root=tmp_path / "workspace",
        model=object(),
        storage_root=storage_root,
        session_state=session,
        runtime_profile=str(session["runtime_profile"]),
    )


def _append_private_learning_events(app, session_id: str) -> tuple[int, list[str]]:
    journal = app.state.coding_run_registry.get(session_id).journal
    after = journal.latest_sequence()
    started = journal.append(
        run_id="run_learning_projection",
        kind="run",
        status="running",
        event_id="learning-private-run-started",
        payload={
            "event": "run_started",
            "surface_context": {"source_path": SOURCE_PATH_SENTINEL},
            "thread_goal": {
                "description": GOAL_SENTINEL,
                "completion_criteria": [CRITERION_SENTINEL],
            },
        },
    )
    evaluated = journal.append(
        run_id="thread-goal",
        kind="harness",
        status="completed",
        event_id="learning-private-goal-evaluated",
        payload={
            "type": "thread_goal_evaluated",
            "goal": {
                "description": GOAL_SENTINEL,
                "completion_criteria": [CRITERION_SENTINEL],
                "evaluation": {"next_action": MODEL_TEXT_SENTINEL},
            },
        },
    )
    terminal = journal.append_terminal_once(
        run_id="run_learning_projection",
        status="completed",
        event_id="learning-private-run-terminal",
        payload={"event": "run_completed"},
    )
    return after, [started.event_id, evaluated.event_id, terminal.event_id]


def test_learning_http_and_websocket_replay_share_safe_public_projection(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        task, receipt = _activate_learning(client)
        session_id = receipt["session_id"]
        _install_runtime(app, tmp_path, session_id)
        after, event_ids = _append_private_learning_events(app, session_id)

        response = client.get(
            f"/api/v1/coding/session/{session_id}/timeline",
            params={"after": after, "limit": 10},
        )
        assert response.status_code == 200
        http_items = response.json()["items"]

        with client.websocket_connect(
            f"/api/v1/coding/{session_id}/stream?after={after}"
        ) as websocket:
            ws_items = [websocket.receive_json() for _ in event_ids]

    assert [item["event_id"] for item in http_items] == event_ids
    assert ws_items == http_items
    assert [item["payload"] for item in http_items] == [
        {
            "type": "custom",
            "task_id": task["task_id"],
            "run_id": "run_learning_projection",
            "status": "running",
        },
        {
            "type": "thread_goal_evaluated",
            "task_id": task["task_id"],
            "run_id": "thread-goal",
            "status": "completed",
        },
        {
            "type": "custom",
            "task_id": task["task_id"],
            "run_id": "run_learning_projection",
            "status": "completed",
        },
    ]
    public = str((http_items, ws_items))
    for sentinel in (
        SOURCE_PATH_SENTINEL,
        GOAL_SENTINEL,
        CRITERION_SENTINEL,
        MODEL_TEXT_SENTINEL,
    ):
        assert sentinel not in public


def test_ordinary_coding_http_and_websocket_replay_keep_internal_payload(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        created = client.post("/api/v1/coding/session", json={}).json()
        session_id = created["session_id"]
        journal = app.state.coding_run_registry.get(session_id).journal
        after = journal.latest_sequence()
        stored = journal.append(
            run_id="run_coding_projection",
            kind="run",
            status="running",
            event_id="coding-private-run-started",
            payload={
                "event": "run_started",
                "surface_context": {"source_path": SOURCE_PATH_SENTINEL},
                "thread_goal": {"description": GOAL_SENTINEL},
            },
        )
        journal.append_terminal_once(
            run_id="run_coding_projection",
            status="completed",
            event_id="coding-private-run-terminal",
            payload={"event": "run_completed"},
        )

        response = client.get(
            f"/api/v1/coding/session/{session_id}/timeline",
            params={"after": after, "limit": 10},
        )
        assert response.status_code == 200
        http_item = response.json()["items"][0]
        with client.websocket_connect(
            f"/api/v1/coding/{session_id}/stream?after={after}"
        ) as websocket:
            ws_item = websocket.receive_json()

    assert http_item["event_id"] == stored.event_id
    assert ws_item == http_item
    assert http_item["payload"]["surface_context"]["source_path"] == SOURCE_PATH_SENTINEL
    assert http_item["payload"]["thread_goal"]["description"] == GOAL_SENTINEL


def test_learning_timeline_fails_closed_when_runtime_identity_drifts(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        _, receipt = _activate_learning(client)
        session_id = receipt["session_id"]
        _install_runtime(app, tmp_path, session_id)
        runtime = app.state.coding_sessions[session_id]
        runtime.session["session_kind"] = "coding"

        response = client.get(f"/api/v1/coding/session/{session_id}/timeline")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "learning_scope_session_mismatch"

        with (
            client.websocket_connect(f"/api/v1/coding/{session_id}/stream?after=0") as websocket,
            pytest.raises(WebSocketDisconnect) as closed,
        ):
            websocket.receive_json()

    assert closed.value.code == 1008
    assert closed.value.reason == "learning_scope_session_mismatch"
