from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.coding import _start_accepted_learning_kickoff
from api.main import create_app
from core.coding.persistence import CodingSessionStore
from core.coding.persistence.session_event_journal import SessionEventJournal
from core.coding.runtime import CodingRuntime


def _app(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return create_app(
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
