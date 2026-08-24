from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api import coding as coding_api
from api.coding import _start_accepted_learning_kickoff
from api.main import create_app
from core.coding.persistence import CodingSessionStore
from core.coding.persistence.session_event_journal import (
    SessionEventJournal,
    SessionThreadGoalConflictError,
)
from core.coding.run_coordinator import ActiveRunConflictError, RunCoordinator
from core.coding.runtime import CodingRuntime


def _app(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return create_app(
        coding_model_factory=lambda: object(),
        coding_workspace_root=workspace,
        coding_storage_root=tmp_path / ".coding",
        coding_default_runtime_profile="legacy",
    )


def _active_task(client: TestClient) -> tuple[dict, dict]:
    task = client.post(
        "/api/v1/learning/tasks/draft",
        json={
            "topic": "学习 durable kickoff 与崩溃恢复",
            "desired_outcome": "能够解释 receipt、Journal 与重放边界",
            "starting_level": "intermediate",
            "time_budget_minutes_per_week": 180,
        },
    ).json()
    activation = client.post(
        f"/api/v1/learning/tasks/{task['task_id']}/activate",
        headers={"Idempotency-Key": f"activate-{task['task_id']}-r1"},
        json={"expected_revision": 1},
    )
    assert activation.status_code == 200
    return task, activation.json()


def _post(client: TestClient, task_id: str, *, key: str):
    return client.post(
        f"/api/v1/learning/tasks/{task_id}/kickoff",
        headers={"Idempotency-Key": key},
        json={"expected_revision": 1},
    )


def test_kickoff_accepts_one_revision_bound_message_and_replays_receipt(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        task, activation = _active_task(client)
        key = f"kickoff-{task['task_id']}-r1"

        response = _post(client, task["task_id"], key=key)

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        receipt = response.json()
        assert receipt["version"] == 1
        assert receipt["task_id"] == task["task_id"]
        assert receipt["task_revision"] == 1
        assert receipt["session_id"] == activation["session_id"]
        assert receipt["receipt_status"] == "accepted"
        assert receipt["stage"] == "accepted"
        assert receipt["dispatch_id"].startswith("lkick_")
        assert receipt["message_id"].startswith("learning-kickoff:")
        assert receipt["acceptance_run_id"].startswith("run_learning_accept_")
        assert receipt["turn_run_id"].startswith("run_learning_turn_")
        assert receipt["content_hash"].startswith("sha256:")
        assert receipt["activation_idempotency_key_hash"].startswith("sha256:")
        assert receipt["kickoff_idempotency_key_hash"].startswith("sha256:")
        assert receipt["accepted_at"] is not None
        assert "durable kickoff" not in response.text

        repeated = _post(client, task["task_id"], key=key)
        canonical = client.get(f"/api/v1/learning/tasks/{task['task_id']}/kickoff")
        conflict = _post(client, task["task_id"], key=key + "-different")

        assert repeated.status_code == 200
        assert repeated.json() == receipt
        assert canonical.status_code == 200
        assert canonical.json() == receipt
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "learning_kickoff_idempotency_conflict"

    journal = SessionEventJournal(tmp_path / ".coding", activation["session_id"])
    events = journal.events_for_run(receipt["acceptance_run_id"])
    assert len(events) == 1
    assert events[0].event_id == receipt["message_id"]
    assert events[0].kind == "user"
    assert events[0].payload == {
        "type": "learning_user_turn",
        "task_id": task["task_id"],
        "run_id": receipt["turn_run_id"],
        "status": "completed",
        "reason_code": "user_content_withheld",
        "dispatch_id": receipt["dispatch_id"],
        "message_id": receipt["message_id"],
    }


def test_kickoff_receipt_survives_app_restart_and_repeated_post(tmp_path: Path) -> None:
    first_app = _app(tmp_path)
    with TestClient(first_app) as client:
        task, _ = _active_task(client)
        key = f"kickoff-{task['task_id']}-r1"
        accepted = _post(client, task["task_id"], key=key).json()

    restarted_app = _app(tmp_path)
    with TestClient(restarted_app) as client:
        canonical = client.get(f"/api/v1/learning/tasks/{task['task_id']}/kickoff")
        repeated = _post(client, task["task_id"], key=key)

    assert canonical.status_code == 200
    assert canonical.json() == accepted
    assert repeated.status_code == 200
    assert repeated.json() == accepted


def test_restarted_coding_stream_lazily_rehydrates_and_starts_accepted_kickoff(
    tmp_path: Path,
) -> None:
    first_app = _app(tmp_path)
    with TestClient(first_app) as client:
        task, activation = _active_task(client)
        accepted = _post(
            client,
            task["task_id"],
            key=f"kickoff-{task['task_id']}-r1",
        ).json()

    journal = SessionEventJournal(tmp_path / ".coding", activation["session_id"])
    assert journal.events_for_run(accepted["turn_run_id"]) == ()

    restarted_app = _app(tmp_path)
    with TestClient(restarted_app) as client:
        assert activation["session_id"] not in restarted_app.state.coding_sessions
        with client.websocket_connect(
            f"/api/v1/coding/{activation['session_id']}/stream?after=0"
        ) as websocket:
            for _ in range(30):
                event = websocket.receive_json()
                if event["kind"] == "terminal" and event["run_id"] == accepted["turn_run_id"]:
                    break
            else:
                raise AssertionError("accepted kickoff did not reach a terminal event")

        assert activation["session_id"] in restarted_app.state.coding_sessions

    turn_events = journal.events_for_run(accepted["turn_run_id"])
    assert sum(event.payload.get("event") == "run_started" for event in turn_events) == 1
    assert sum(event.kind == "terminal" for event in turn_events) == 1


def test_learning_websocket_handshake_waits_for_slow_runtime_rehydrate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Learning exposes the shared session only after its runtime is REST-ready."""
    first_app = _app(tmp_path)
    with TestClient(first_app) as client:
        task, activation = _active_task(client)
        accepted = _post(
            client,
            task["task_id"],
            key=f"kickoff-{task['task_id']}-r1",
        ).json()

    restarted_app = _app(tmp_path)
    original_load = coding_api.load_account_model_context
    rehydrate_started = threading.Event()
    allow_rehydrate = threading.Event()
    websocket_opened = threading.Event()
    worker_errors: list[BaseException] = []

    async def slow_load(connection, *, include_credentials=False):  # type: ignore[no-untyped-def]
        rehydrate_started.set()
        await asyncio.to_thread(allow_rehydrate.wait)
        return await original_load(connection, include_credentials=include_credentials)

    monkeypatch.setattr(coding_api, "load_account_model_context", slow_load)
    with TestClient(restarted_app) as client:

        def connect() -> None:
            try:
                with client.websocket_connect(
                    f"/api/v1/coding/{activation['session_id']}/stream?after=0"
                ) as websocket:
                    websocket_opened.set()
                    for _ in range(30):
                        event = websocket.receive_json()
                        if (
                            event["kind"] == "terminal"
                            and event["run_id"] == accepted["turn_run_id"]
                        ):
                            return
                    raise AssertionError("accepted kickoff did not reach a terminal event")
            except BaseException as exc:  # pragma: no cover - surfaced by assertion
                worker_errors.append(exc)

        worker = threading.Thread(target=connect)
        worker.start()
        assert rehydrate_started.wait(timeout=2)
        opened_before_runtime_ready = websocket_opened.wait(timeout=0.05)
        absent_before_release = activation["session_id"] not in restarted_app.state.coding_sessions

        allow_rehydrate.set()
        assert websocket_opened.wait(timeout=2)
        pending = client.get(f"/api/v1/coding/{activation['session_id']}/approval/pending")
        worker.join(timeout=3)

    assert not opened_before_runtime_ready
    assert absent_before_release
    assert pending.status_code == 200
    assert not worker.is_alive()
    assert worker_errors == []


@pytest.mark.parametrize("point", ["after_intent", "after_journal", "after_accepted"])
def test_kickoff_failure_points_recover_without_duplicate_message(
    tmp_path: Path,
    point: str,
) -> None:
    first_app = _app(tmp_path)

    def inject(current: str, _record) -> None:  # type: ignore[no-untyped-def]
        if current == point:
            raise RuntimeError(f"injected {point}")

    first_app.state.learning_kickoff_service.failure_injector = inject
    with TestClient(first_app) as client:
        task, activation = _active_task(client)
        key = f"kickoff-{task['task_id']}-r1"
        failed = _post(client, task["task_id"], key=key)

        assert failed.status_code == 503
        assert failed.json()["detail"]["code"] == "learning_kickoff_dispatch_failed"
        partial = client.get(f"/api/v1/learning/tasks/{task['task_id']}/kickoff")
        assert partial.status_code == 200
        assert partial.json()["receipt_status"] == (
            "accepted" if point == "after_accepted" else "dispatching"
        )

    restarted_app = _app(tmp_path)
    with TestClient(restarted_app) as client:
        recovered = _post(client, task["task_id"], key=key)
        canonical = client.get(f"/api/v1/learning/tasks/{task['task_id']}/kickoff")

    assert recovered.status_code == 200
    assert recovered.json()["receipt_status"] == "accepted"
    assert canonical.json() == recovered.json()
    journal = SessionEventJournal(tmp_path / ".coding", activation["session_id"])
    assert len(journal.events_for_run(recovered.json()["acceptance_run_id"])) == 1


def test_kickoff_requires_active_revision_and_unique_key_scope(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        draft = client.post(
            "/api/v1/learning/tasks/draft",
            json={
                "topic": "尚未激活的任务",
                "desired_outcome": "验证失败边界",
                "starting_level": "beginner",
                "time_budget_minutes_per_week": 60,
            },
        ).json()
        not_active = _post(client, draft["task_id"], key="kickoff-before-active")
        assert not_active.status_code == 409
        assert not_active.json()["detail"]["code"] == "learning_kickoff_activation_required"

        first, _ = _active_task(client)
        second, _ = _active_task(client)
        shared_key = "kickoff-shared-across-tasks"
        accepted = _post(client, first["task_id"], key=shared_key)
        reused = _post(client, second["task_id"], key=shared_key)

        assert accepted.status_code == 200
        assert reused.status_code == 409
        assert reused.json()["detail"]["code"] == "learning_kickoff_idempotency_conflict"


def test_kickoff_get_fails_closed_when_canonical_task_binding_drifts(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        task, _ = _active_task(client)
        accepted = _post(
            client,
            task["task_id"],
            key=f"kickoff-{task['task_id']}-r1",
        )
        assert accepted.status_code == 200

        database = tmp_path / ".coding" / "learning-tasks.sqlite3"
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT payload_json FROM learning_tasks WHERE task_id = ?",
                (task["task_id"],),
            ).fetchone()
            assert row is not None
            payload = json.loads(str(row[0]))
            payload["status"] = "completed"
            connection.execute(
                "UPDATE learning_tasks SET status = ?, payload_json = ? WHERE task_id = ?",
                (
                    "completed",
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    task["task_id"],
                ),
            )

        canonical = client.get(f"/api/v1/learning/tasks/{task['task_id']}/kickoff")

    assert canonical.status_code == 409
    assert canonical.json()["detail"]["code"] == "learning_kickoff_binding_conflict"


def test_kickoff_get_fails_closed_when_canonical_task_is_missing(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        task, _ = _active_task(client)
        accepted = _post(
            client,
            task["task_id"],
            key=f"kickoff-{task['task_id']}-r1",
        )
        assert accepted.status_code == 200

        database = tmp_path / ".coding" / "learning-tasks.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM learning_tasks WHERE task_id = ?", (task["task_id"],))

        canonical = client.get(f"/api/v1/learning/tasks/{task['task_id']}/kickoff")

    assert canonical.status_code == 409
    assert canonical.json()["detail"]["code"] == "learning_kickoff_binding_conflict"


def test_coding_stream_fails_closed_when_kickoff_task_is_missing(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        task, activation = _active_task(client)
        accepted = _post(
            client,
            task["task_id"],
            key=f"kickoff-{task['task_id']}-r1",
        )
        assert accepted.status_code == 200
        session = CodingSessionStore(tmp_path / ".coding" / "sessions").load(
            activation["session_id"]
        )
        app.state.coding_sessions[activation["session_id"]] = CodingRuntime(
            session_id=activation["session_id"],
            workspace_root=tmp_path / "workspace",
            model=object(),
            storage_root=tmp_path / ".coding",
            session_state=session,
            runtime_profile=str(session["runtime_profile"]),
        )

        database = tmp_path / ".coding" / "learning-tasks.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM learning_tasks WHERE task_id = ?", (task["task_id"],))

        with (
            client.websocket_connect(
                f"/api/v1/coding/{activation['session_id']}/stream?after=0"
            ) as websocket,
            pytest.raises(WebSocketDisconnect) as closed,
        ):
            websocket.receive_json()

    assert closed.value.code == 1008
    assert closed.value.reason == "learning_kickoff_binding_conflict"


def test_coding_stream_consumes_only_accepted_kickoff_once(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        task, activation = _active_task(client)
        session = CodingSessionStore(tmp_path / ".coding" / "sessions").load(
            activation["session_id"]
        )
        runtime = CodingRuntime(
            session_id=activation["session_id"],
            workspace_root=tmp_path / "workspace",
            model=object(),
            storage_root=tmp_path / ".coding",
            session_state=session,
            runtime_profile=str(session["runtime_profile"]),
        )
        app.state.coding_sessions[activation["session_id"]] = runtime

        async def before_and_after_acceptance():  # type: ignore[no-untyped-def]
            coordinator = await app.state.coding_run_registry.hydrate(activation["session_id"])
            before = await _start_accepted_learning_kickoff(app, runtime, coordinator)
            accepted = _post(
                client,
                task["task_id"],
                key=f"kickoff-{task['task_id']}-r1",
            ).json()
            started = await _start_accepted_learning_kickoff(app, runtime, coordinator)
            assert started is not None
            await started
            repeated = await _start_accepted_learning_kickoff(app, runtime, coordinator)
            return before, accepted, repeated, coordinator

        before, accepted, repeated, coordinator = asyncio.run(before_and_after_acceptance())

    assert before is None
    assert repeated is None
    turn_events = coordinator.journal.events_for_run(accepted["turn_run_id"])
    assert sum(event.payload.get("event") == "run_started" for event in turn_events) == 1
    assert sum(event.kind == "terminal" for event in turn_events) == 1
    assert all(event.event_id != accepted["message_id"] for event in turn_events)
    acceptance_events = coordinator.journal.events_for_run(accepted["acceptance_run_id"])
    assert [event.event_id for event in acceptance_events] == [accepted["message_id"]]


def test_concurrent_kickoff_stale_check_converges_to_same_terminal_run(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        task, activation = _active_task(client)
        accepted = _post(
            client,
            task["task_id"],
            key=f"kickoff-{task['task_id']}-r1",
        ).json()
        session = CodingSessionStore(tmp_path / ".coding" / "sessions").load(
            activation["session_id"]
        )
        runtime = CodingRuntime(
            session_id=activation["session_id"],
            workspace_root=tmp_path / "workspace",
            model=object(),
            storage_root=tmp_path / ".coding",
            session_state=session,
            runtime_profile=str(session["runtime_profile"]),
        )

        class DelayedRunCoordinator(RunCoordinator):
            def __init__(self, journal: SessionEventJournal) -> None:
                super().__init__(journal, owner_id="second-app", owner_pid=os.getpid())
                self.ready = asyncio.Event()
                self.release = asyncio.Event()

            async def start_run(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                self.ready.set()
                await self.release.wait()
                return await super().start_run(*args, **kwargs)

        first = RunCoordinator(
            SessionEventJournal(tmp_path / ".coding", activation["session_id"]),
            owner_id="first-app",
            owner_pid=os.getpid(),
        )
        second = DelayedRunCoordinator(
            SessionEventJournal(tmp_path / ".coding", activation["session_id"])
        )

        async def exercise_race():  # type: ignore[no-untyped-def]
            stale_attempt = asyncio.create_task(
                _start_accepted_learning_kickoff(app, runtime, second)
            )
            await second.ready.wait()
            started = await _start_accepted_learning_kickoff(app, runtime, first)
            assert started is not None
            await started
            second.release.set()
            replay = await stale_attempt
            return replay

        replay = asyncio.run(exercise_race())

    assert replay is None
    events = first.journal.events_for_run(accepted["turn_run_id"])
    assert sum(event.payload.get("event") == "run_started" for event in events) == 1
    assert sum(event.kind == "terminal" for event in events) == 1


def test_kickoff_does_not_swallow_unrelated_thread_goal_conflict(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        task, activation = _active_task(client)
        _post(
            client,
            task["task_id"],
            key=f"kickoff-{task['task_id']}-r1",
        )
        session = CodingSessionStore(tmp_path / ".coding" / "sessions").load(
            activation["session_id"]
        )
        runtime = CodingRuntime(
            session_id=activation["session_id"],
            workspace_root=tmp_path / "workspace",
            model=object(),
            storage_root=tmp_path / ".coding",
            session_state=session,
            runtime_profile=str(session["runtime_profile"]),
        )
        coordinator = RunCoordinator(
            SessionEventJournal(tmp_path / ".coding", activation["session_id"])
        )

        async def conflict(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise SessionThreadGoalConflictError(2)

        coordinator.start_run = conflict  # type: ignore[method-assign]

        with pytest.raises(SessionThreadGoalConflictError):
            asyncio.run(_start_accepted_learning_kickoff(app, runtime, coordinator))


def test_kickoff_does_not_swallow_unrelated_active_run_conflict(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        task, activation = _active_task(client)
        _post(
            client,
            task["task_id"],
            key=f"kickoff-{task['task_id']}-r1",
        )
        session = CodingSessionStore(tmp_path / ".coding" / "sessions").load(
            activation["session_id"]
        )
        runtime = CodingRuntime(
            session_id=activation["session_id"],
            workspace_root=tmp_path / "workspace",
            model=object(),
            storage_root=tmp_path / ".coding",
            session_state=session,
            runtime_profile=str(session["runtime_profile"]),
        )
        coordinator = RunCoordinator(
            SessionEventJournal(tmp_path / ".coding", activation["session_id"])
        )

        async def conflict(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise ActiveRunConflictError("another run is active")

        coordinator.start_run = conflict  # type: ignore[method-assign]

        with pytest.raises(ActiveRunConflictError):
            asyncio.run(_start_accepted_learning_kickoff(app, runtime, coordinator))
