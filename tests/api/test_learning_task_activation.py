from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app
from core.coding.persistence import CodingSessionStore, TurnPlanStore
from core.harness.thread_goal import ThreadGoalService


class _AvailablePort:
    available = True


def _app(tmp_path: Path, *, web_search_available: bool = False):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return create_app(
        coding_workspace_root=workspace,
        coding_storage_root=tmp_path / ".coding",
        coding_default_runtime_profile="legacy",
        coding_web_search_port=_AvailablePort() if web_search_available else None,  # type: ignore[arg-type]
        coding_web_fetch_port=None,
    )


def _ready_draft(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/learning/tasks/draft",
        json={
            "topic": "系统学习阳明心学，不要联网",
            "desired_outcome": "能够解释核心概念并比较主要争议",
            "starting_level": "beginner",
            "time_budget_minutes_per_week": 240,
        },
    )
    assert response.status_code == 201
    return response.json()


def _rewrite_activation_as_legacy(database: Path, task_id: str) -> dict:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT receipt_json FROM learning_task_activations WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        assert row is not None
        current = json.loads(str(row[0]))
        assert "plan_id" not in current
        assert "plan_hash" not in current
        legacy = dict(current)
        legacy["version"] = 1
        legacy["plan_id"] = legacy.pop("turn_context_plan_id")
        legacy["plan_hash"] = legacy.pop("turn_context_plan_hash")
        legacy.pop("learning_plan_id")
        legacy.pop("learning_plan_hash")
        legacy.pop("dag_hash")
        if legacy["stage"] == "turn_context_plan":
            legacy["stage"] = "plan"
        connection.execute(
            "UPDATE learning_task_activations SET receipt_json = ?, stage = ? WHERE task_id = ?",
            (
                json.dumps(legacy, separators=(",", ":"), sort_keys=True),
                legacy["stage"],
                task_id,
            ),
        )
        connection.commit()
    return current


def test_activate_returns_one_revision_bound_kickoff_receipt(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        task = _ready_draft(client)
        path = f"/api/v1/learning/tasks/{task['task_id']}/activate"
        first_response = client.post(
            path,
            headers={"Idempotency-Key": "activate-yangming-v1"},
            json={"expected_revision": 1},
        )

        assert first_response.status_code == 200
        assert first_response.headers["cache-control"] == "no-store"
        receipt = first_response.json()
        assert receipt["task_id"] == task["task_id"]
        assert receipt["task_revision"] == 1
        assert receipt["receipt_status"] == "active"
        assert receipt["thread_goal_revision"] == 1
        assert receipt["learning_plan_id"] is None
        assert receipt["learning_plan_hash"] is None
        assert receipt["turn_context_plan_id"].startswith("turnplan_")
        assert receipt["turn_context_plan_hash"].startswith("sha256:")
        assert receipt["dag_hash"] is None
        assert receipt["plan_id"] == receipt["turn_context_plan_id"]
        assert receipt["plan_hash"] == receipt["turn_context_plan_hash"]
        assert receipt["capability_revision"].startswith("lcap_")
        assert receipt["allowed_capabilities"] == ["local:knowledge_search"]
        assert receipt["learning_goal_ref"]["goal_id"].startswith("learning-task-")
        assert receipt["completed_at"] is not None

        repeated = client.post(
            path,
            headers={"Idempotency-Key": "activate-yangming-v1"},
            json={"expected_revision": 1},
        )
        assert repeated.status_code == 200
        assert repeated.json() == receipt

        conflicting = client.post(
            path,
            headers={"Idempotency-Key": "activate-yangming-v2"},
            json={"expected_revision": 1},
        )
        assert conflicting.status_code == 409
        assert conflicting.json()["detail"]["code"] == "activation_idempotency_conflict"

        assert client.get(f"/api/v1/learning/tasks/{task['task_id']}/activation").json() == receipt
        resumed = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/resume",
            json={"expected_revision": 1},
        )
        assert resumed.status_code == 200
        assert resumed.json() == receipt

        active_task = client.get(f"/api/v1/learning/tasks/{task['task_id']}").json()
        assert active_task["status"] == "active"
        assert active_task["learning_goal_ref"] == receipt["learning_goal_ref"]

    record = app.state.learning_activation_service.get(owner_id="local", task_id=task["task_id"])
    session = CodingSessionStore(tmp_path / ".coding" / "sessions").load(receipt["session_id"])
    assert session["session_kind"] == "learning"
    assert session["learning_task_id"] == task["task_id"]
    assert session["learning_task_revision"] == 1
    assert session["archived"] is False
    assert session["history"] == []

    journal = app.state.coding_run_registry.get(receipt["session_id"]).journal
    goal = ThreadGoalService(journal).get()
    assert goal is not None
    assert goal["learning_goal"]["goal_id"] == receipt["learning_goal_ref"]["goal_id"]
    assert goal["learning_goal"]["goal_revision"] == receipt["learning_goal_ref"]["goal_revision"]

    plan = TurnPlanStore(tmp_path / ".coding", receipt["session_id"]).load_for_run(
        record.kickoff_run_id
    )
    assert plan is not None
    assert plan.plan_id == receipt["turn_context_plan_id"]
    assert plan.plan_hash == receipt["turn_context_plan_hash"]
    payload = plan.to_payload()
    assert payload["admission"]["task_kind"] == "learning"
    assert payload["tools"]["allowed_capabilities"] == ["local:knowledge_search"]
    assert "local:run_shell" not in payload["tools"]["allowed_capabilities"]
    assert "subagent:practice" not in payload["tools"]["allowed_capabilities"]


def test_activation_requires_confirmation_revision_and_idempotency_key(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        draft = client.post(
            "/api/v1/learning/tasks/draft",
            json={"topic": "学习金融基础"},
        ).json()
        path = f"/api/v1/learning/tasks/{draft['task_id']}/activate"

        assert client.post(path, json={"expected_revision": 1}).status_code == 422
        not_ready = client.post(
            path,
            headers={"Idempotency-Key": "finance-v1"},
            json={"expected_revision": 1},
        )
        assert not_ready.status_code == 409
        assert not_ready.json()["detail"]["code"] == "learning_task_not_ready"


def test_idempotency_key_cannot_be_reused_for_another_task(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        first = _ready_draft(client)
        second = _ready_draft(client)
        activated = client.post(
            f"/api/v1/learning/tasks/{first['task_id']}/activate",
            headers={"Idempotency-Key": "one-owner-key"},
            json={"expected_revision": 1},
        )
        conflict = client.post(
            f"/api/v1/learning/tasks/{second['task_id']}/activate",
            headers={"Idempotency-Key": "one-owner-key"},
            json={"expected_revision": 1},
        )

        assert activated.status_code == 200
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "activation_idempotency_conflict"
        second_task = client.get(f"/api/v1/learning/tasks/{second['task_id']}").json()
        assert second_task["status"] == "draft"


def test_startup_reconciliation_completes_a_failed_receipt_and_unarchives_session(
    tmp_path: Path,
) -> None:
    first_app = _app(tmp_path)

    def fail_before_receipt(point: str, _activation) -> None:  # type: ignore[no-untyped-def]
        if point == "before_receipt":
            raise RuntimeError("injected before receipt")

    first_app.state.learning_activation_service.failure_injector = fail_before_receipt
    with TestClient(first_app) as client:
        task = _ready_draft(client)
        failed = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/activate",
            headers={"Idempotency-Key": "restart-yangming-v1"},
            json={"expected_revision": 1},
        )
        assert failed.status_code == 503
        partial = client.get(f"/api/v1/learning/tasks/{task['task_id']}/activation").json()
        assert partial["receipt_status"] == "activation_failed"

    store = CodingSessionStore(tmp_path / ".coding" / "sessions")
    assert store.load(partial["session_id"])["archived"] is True
    _rewrite_activation_as_legacy(
        tmp_path / ".coding" / "learning-tasks.sqlite3",
        task["task_id"],
    )

    restarted_app = _app(tmp_path)
    with TestClient(restarted_app) as restarted:
        active = restarted.get(f"/api/v1/learning/tasks/{task['task_id']}/activation")
        assert active.status_code == 200
        assert active.json()["receipt_status"] == "active"

    assert store.load(partial["session_id"])["archived"] is False


def test_startup_reconciliation_fails_closed_when_frozen_plan_catalog_changes(
    tmp_path: Path,
) -> None:
    first_app = _app(tmp_path)

    def fail_before_receipt(point: str, _activation) -> None:  # type: ignore[no-untyped-def]
        if point == "before_receipt":
            raise RuntimeError("injected before receipt")

    first_app.state.learning_activation_service.failure_injector = fail_before_receipt
    with TestClient(first_app) as client:
        task = _ready_draft(client)
        failed = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/activate",
            headers={"Idempotency-Key": "catalog-drift-v1"},
            json={"expected_revision": 1},
        )
        assert failed.status_code == 503
        original = client.get(f"/api/v1/learning/tasks/{task['task_id']}/activation").json()

    restarted_app = _app(tmp_path, web_search_available=True)
    with TestClient(restarted_app) as restarted:
        receipt = restarted.get(f"/api/v1/learning/tasks/{task['task_id']}/activation").json()

    assert receipt["receipt_status"] == "activation_failed"
    assert receipt["failure_code"] == "learning_activation_plan_conflict"
    assert receipt["turn_context_plan_hash"] == original["turn_context_plan_hash"]
    store = CodingSessionStore(tmp_path / ".coding" / "sessions")
    assert store.load(receipt["session_id"])["archived"] is True


def test_activation_rejects_a_colliding_session_without_mutating_it(tmp_path: Path) -> None:
    app = _app(tmp_path)

    def fail_after_session(point: str, _activation) -> None:  # type: ignore[no-untyped-def]
        if point == "after_session":
            raise RuntimeError("injected after session")

    app.state.learning_activation_service.failure_injector = fail_after_session
    with TestClient(app) as client:
        task = _ready_draft(client)
        path = f"/api/v1/learning/tasks/{task['task_id']}/activate"
        failed = client.post(
            path,
            headers={"Idempotency-Key": "session-collision-v1"},
            json={"expected_revision": 1},
        )
        assert failed.status_code == 503
        receipt = client.get(f"/api/v1/learning/tasks/{task['task_id']}/activation").json()

        store = CodingSessionStore(tmp_path / ".coding" / "sessions")
        collision = store.load(receipt["session_id"])
        collision["workspace_root"] = str(tmp_path / "another-workspace")
        collision["owner_user_id"] = "another-owner"
        collision["archived"] = False
        store.save(collision)
        app.state.learning_activation_service.failure_injector = None

        retried = client.post(
            path,
            headers={"Idempotency-Key": "session-collision-v1"},
            json={"expected_revision": 1},
        )

    assert retried.status_code == 409
    assert retried.json()["detail"]["code"] == "learning_activation_session_conflict"
    preserved = store.load(receipt["session_id"])
    assert preserved["owner_user_id"] == "another-owner"
    assert preserved["archived"] is False


def test_corrupt_activation_receipt_returns_a_stable_conflict(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        task = _ready_draft(client)
        activated = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/activate",
            headers={"Idempotency-Key": "corrupt-receipt-v1"},
            json={"expected_revision": 1},
        )
        assert activated.status_code == 200
        database = tmp_path / ".coding" / "learning-tasks.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE learning_task_activations SET receipt_json = ? WHERE task_id = ?",
                ('{"version":1}', task["task_id"]),
            )
            connection.commit()

        response = client.get(f"/api/v1/learning/tasks/{task['task_id']}/activation")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "learning_activation_corrupt"


def test_legacy_plan_identity_is_read_as_turn_context_identity_only(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        task = _ready_draft(client)
        activated = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/activate",
            headers={"Idempotency-Key": "legacy-plan-hash-v1"},
            json={"expected_revision": 1},
        )
        assert activated.status_code == 200
        current = activated.json()

        database = tmp_path / ".coding" / "learning-tasks.sqlite3"
        new_receipt = _rewrite_activation_as_legacy(database, task["task_id"])
        assert new_receipt["learning_plan_id"] is None
        assert new_receipt["learning_plan_hash"] is None
        assert new_receipt["dag_hash"] is None

        migrated = client.get(f"/api/v1/learning/tasks/{task['task_id']}/activation")

    assert migrated.status_code == 200, migrated.json()
    receipt = migrated.json()
    assert receipt["version"] == 2
    assert receipt["learning_plan_id"] is None
    assert receipt["learning_plan_hash"] is None
    assert receipt["turn_context_plan_id"] == current["turn_context_plan_id"]
    assert receipt["turn_context_plan_hash"] == current["turn_context_plan_hash"]
    assert receipt["dag_hash"] is None
    assert receipt["plan_id"] == receipt["turn_context_plan_id"]
    assert receipt["plan_hash"] == receipt["turn_context_plan_hash"]
