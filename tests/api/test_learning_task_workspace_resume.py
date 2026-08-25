from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from core.coding.memory import workspace_id_from_path
from core.coding.persistence import CodingSessionStore, TurnPlanStore
from core.harness.turn_context_plan import TurnContextPlan


class _AvailablePort:
    available = True


def _app(root: Path, workspace_name: str, *, web_search_available: bool = False):
    workspace = root / workspace_name
    workspace.mkdir(exist_ok=True)
    return create_app(
        coding_workspace_root=workspace,
        coding_storage_root=root / ".coding",
        coding_default_runtime_profile="legacy",
        coding_web_search_port=_AvailablePort() if web_search_available else None,  # type: ignore[arg-type]
    )


def _ready_draft(client: TestClient, *, topic: str) -> dict:
    response = client.post(
        "/api/v1/learning/tasks/draft",
        json={
            "topic": topic,
            "desired_outcome": "能够解释核心概念并完成一次复核",
            "starting_level": "beginner",
            "time_budget_minutes_per_week": 180,
        },
    )
    assert response.status_code == 201
    return response.json()


def _activate(client: TestClient, task: dict, *, key: str) -> dict:
    response = client.post(
        f"/api/v1/learning/tasks/{task['task_id']}/activate",
        headers={"Idempotency-Key": key},
        json={"expected_revision": task["task_revision"]},
    )
    assert response.status_code == 200, response.json()
    return response.json()


def _create_l0_database_schema(root: Path) -> None:
    database = root / ".coding" / "learning-tasks.sqlite3"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE learning_tasks (
                owner_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                task_revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (owner_id, task_id)
            );
            CREATE TABLE learning_task_activations (
                owner_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                task_revision INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL,
                session_id TEXT NOT NULL,
                kickoff_run_id TEXT NOT NULL,
                receipt_json TEXT NOT NULL,
                status TEXT NOT NULL,
                stage TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                PRIMARY KEY (owner_id, task_id, task_revision),
                UNIQUE (owner_id, idempotency_key)
            );
            """
        )
        connection.commit()


def test_learning_api_isolates_all_task_operations_by_owner_and_workspace(
    tmp_path: Path,
) -> None:
    _create_l0_database_schema(tmp_path)
    app_a = _app(tmp_path, "workspace-a")
    app_b = _app(tmp_path, "workspace-b")

    with TestClient(app_a) as client_a:
        task_a = _ready_draft(client_a, topic="学习 workspace A")
        workspace_a = workspace_id_from_path(tmp_path / "workspace-a")
        assert task_a["workspace_id"] == workspace_a
        listed_a = client_a.get("/api/v1/learning/tasks")
        assert listed_a.status_code == 200
        assert [item["task_id"] for item in listed_a.json()] == [task_a["task_id"]]
        activated_a = client_a.post(
            f"/api/v1/learning/tasks/{task_a['task_id']}/activate",
            headers={"Idempotency-Key": "workspace-shared-key"},
            json={"expected_revision": 1},
        )
        assert activated_a.status_code == 200
        assert activated_a.json()["workspace_id"] == workspace_a

    with TestClient(app_b) as client_b:
        assert client_b.get("/api/v1/learning/tasks").json() == []
        assert client_b.get(f"/api/v1/learning/tasks/{task_a['task_id']}").status_code == 404
        assert (
            client_b.patch(
                f"/api/v1/learning/tasks/{task_a['task_id']}",
                json={"expected_revision": 1, "desired_outcome": "越权修改"},
            ).status_code
            == 404
        )
        assert (
            client_b.post(
                f"/api/v1/learning/tasks/{task_a['task_id']}/activate",
                headers={"Idempotency-Key": "workspace-shared-key"},
                json={"expected_revision": 1},
            ).status_code
            == 404
        )
        assert (
            client_b.get(f"/api/v1/learning/tasks/{task_a['task_id']}/activation").status_code
            == 404
        )
        assert (
            client_b.post(
                f"/api/v1/learning/tasks/{task_a['task_id']}/resume",
                json={"expected_revision": 1},
            ).status_code
            == 404
        )

        task_b = _ready_draft(client_b, topic="学习 workspace B")
        activated_b = client_b.post(
            f"/api/v1/learning/tasks/{task_b['task_id']}/activate",
            headers={"Idempotency-Key": "workspace-shared-key"},
            json={"expected_revision": 1},
        )
        assert activated_b.status_code == 200
        assert activated_b.json()["workspace_id"] == workspace_id_from_path(
            tmp_path / "workspace-b"
        )
        assert activated_b.json()["session_id"] != activated_a.json()["session_id"]


def test_resume_reloads_canonical_resources_and_rejects_missing_turn_plan(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path, "workspace")
    with TestClient(app) as client:
        task = _ready_draft(client, topic="学习恢复校验")
        receipt = _activate(client, task, key="missing-plan")
        record = app.state.learning_activation_service.get(
            owner_id="local",
            workspace_id=task["workspace_id"],
            task_id=task["task_id"],
        )
        plan_store = TurnPlanStore(tmp_path / ".coding", receipt["session_id"])
        with sqlite3.connect(plan_store.path) as connection:
            connection.execute(
                "DELETE FROM turn_context_plans WHERE run_id = ?", (record.kickoff_run_id,)
            )
            connection.commit()

        resumed = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/resume",
            json={"expected_revision": 1},
        )

    assert resumed.status_code == 409
    assert resumed.json()["detail"]["code"] == "learning_resume_validation_failed"


def test_resume_rejects_session_plan_source_and_l0_identity_drift(tmp_path: Path) -> None:
    cases = (
        "missing_session",
        "session_owner",
        "tampered_plan",
        "source_policy",
        "learning_plan_hash",
        "dag_hash",
    )
    for case in cases:
        root = tmp_path / case
        root.mkdir()
        app = _app(root, "workspace")
        with TestClient(app) as client:
            task = _ready_draft(client, topic=f"学习恢复校验 {case}")
            receipt = _activate(client, task, key=f"resume-{case}")
            assert receipt["source_policy_snapshot"] == {
                "knowledge": "preferred",
                "web": "allowed_when_insufficient",
                "domains": [],
                "freshness": "all",
            }
            assert receipt["source_policy_revision"].startswith("lsrc_")
            record = app.state.learning_activation_service.get(
                owner_id="local",
                workspace_id=task["workspace_id"],
                task_id=task["task_id"],
            )
            session_store = CodingSessionStore(root / ".coding" / "sessions")
            database = root / ".coding" / "learning-tasks.sqlite3"
            if case == "missing_session":
                session_store.path(receipt["session_id"]).unlink()
            elif case == "session_owner":
                session = session_store.load(receipt["session_id"])
                session["learning_owner_id"] = "tampered-owner"
                session_store.save(session)
            elif case == "tampered_plan":
                plan_store = TurnPlanStore(root / ".coding", receipt["session_id"])
                with sqlite3.connect(plan_store.path) as connection:
                    connection.execute(
                        "UPDATE turn_context_plans SET plan_hash = ? WHERE run_id = ?",
                        ("sha256:" + "0" * 64, record.kickoff_run_id),
                    )
                    connection.commit()
            elif case == "source_policy":
                with sqlite3.connect(database) as connection:
                    row = connection.execute(
                        "SELECT payload_json FROM learning_tasks WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone()
                    assert row is not None
                    payload = json.loads(str(row[0]))
                    payload["source_policy"]["freshness"] = "current"
                    connection.execute(
                        "UPDATE learning_tasks SET payload_json = ? WHERE task_id = ?",
                        (json.dumps(payload, sort_keys=True), task["task_id"]),
                    )
                    connection.commit()
            else:
                with sqlite3.connect(database) as connection:
                    row = connection.execute(
                        "SELECT receipt_json FROM learning_task_activations WHERE task_id = ?",
                        (task["task_id"],),
                    ).fetchone()
                    assert row is not None
                    payload = json.loads(str(row[0]))
                    payload[case] = "sha256:" + "f" * 64
                    connection.execute(
                        "UPDATE learning_task_activations SET receipt_json = ? WHERE task_id = ?",
                        (json.dumps(payload, sort_keys=True), task["task_id"]),
                    )
                    connection.commit()

            resumed = client.post(
                f"/api/v1/learning/tasks/{task['task_id']}/resume",
                json={"expected_revision": 1},
            )
        assert resumed.status_code == 409, (case, resumed.json())


def test_resume_rejects_catalog_and_capability_drift_after_restart(tmp_path: Path) -> None:
    first_app = _app(tmp_path, "workspace")
    with TestClient(first_app) as client:
        task = _ready_draft(client, topic="学习目录漂移")
        _activate(client, task, key="catalog-drift")

    restarted_app = _app(tmp_path, "workspace", web_search_available=True)
    with TestClient(restarted_app) as client:
        resumed = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/resume",
            json={"expected_revision": 1},
        )

    assert resumed.status_code == 409
    assert resumed.json()["detail"]["code"] == "learning_resume_validation_failed"


def _rewrite_as_workspace_legacy(root: Path, task_id: str) -> None:
    database = root / ".coding" / "learning-tasks.sqlite3"
    with sqlite3.connect(database) as connection:
        task_row = connection.execute(
            "SELECT payload_json FROM learning_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        assert task_row is not None
        task_payload = json.loads(str(task_row[0]))
        task_payload.pop("workspace_id")
        connection.execute(
            "UPDATE learning_tasks SET workspace_id = NULL, payload_json = ? WHERE task_id = ?",
            (json.dumps(task_payload, sort_keys=True), task_id),
        )
        activation_row = connection.execute(
            "SELECT receipt_json FROM learning_task_activations WHERE task_id = ?", (task_id,)
        ).fetchone()
        if activation_row is not None:
            receipt = json.loads(str(activation_row[0]))
            receipt["version"] = 2
            receipt.pop("workspace_id")
            receipt.pop("source_policy_snapshot")
            receipt.pop("source_policy_revision")
            connection.execute(
                "UPDATE learning_task_activations SET workspace_id = NULL, receipt_json = ? "
                "WHERE task_id = ?",
                (json.dumps(receipt, sort_keys=True), task_id),
            )
        connection.commit()


def _rewrite_turn_plan_as_a6d_v2(root: Path, receipt: dict) -> TurnContextPlan:
    store = TurnPlanStore(root / ".coding", receipt["session_id"])
    current = store.load_for_run(receipt["kickoff_run_id"])
    assert current is not None
    payload = current.to_payload()
    payload["retrieval"].pop("source_policy_revision")
    payload["resume"].pop("catalog_revision")
    payload["resume"].pop("source_policy_revision")
    legacy = TurnContextPlan.create(
        **current.identity_kwargs(),
        created_at=current.created_at,
        admission=payload["admission"],
        prompt=payload["prompt"],
        context_refs=payload["context_refs"],
        retrieval=payload["retrieval"],
        tools=payload["tools"],
        execution=payload["execution"],
        resume=payload["resume"],
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE turn_context_plans SET plan_hash = ?, payload_json = ? WHERE run_id = ?",
            (legacy.plan_hash, legacy.payload_json, legacy.run_id),
        )
        connection.commit()
    database = root / ".coding" / "learning-tasks.sqlite3"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT receipt_json FROM learning_task_activations WHERE task_id = ?",
            (receipt["task_id"],),
        ).fetchone()
        assert row is not None
        activation = json.loads(str(row[0]))
        activation["turn_context_plan_hash"] = legacy.plan_hash
        connection.execute(
            "UPDATE learning_task_activations SET receipt_json = ? WHERE task_id = ?",
            (json.dumps(activation, sort_keys=True), receipt["task_id"]),
        )
        connection.commit()
    return legacy


def _tamper_task_identity(root: Path, task_id: str, field: str) -> None:
    database = root / ".coding" / "learning-tasks.sqlite3"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT payload_json FROM learning_tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        assert row is not None
        payload = json.loads(str(row[0]))
        payload[field] = "future-identity"
        connection.execute(
            "UPDATE learning_tasks SET payload_json = ? WHERE task_id = ?",
            (json.dumps(payload, sort_keys=True), task_id),
        )
        connection.commit()


def test_startup_backfills_only_uniquely_validated_active_workspace_legacy(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path, "workspace")
    with TestClient(app) as client:
        task = _ready_draft(client, topic="学习 legacy active")
        receipt = _activate(client, task, key="legacy-active")

    record = app.state.learning_activation_service.get(
        owner_id="local",
        workspace_id=task["workspace_id"],
        task_id=task["task_id"],
    )
    _rewrite_turn_plan_as_a6d_v2(
        tmp_path,
        {**receipt, "task_id": task["task_id"], "kickoff_run_id": record.kickoff_run_id},
    )

    session_store = CodingSessionStore(tmp_path / ".coding" / "sessions")
    session = session_store.load(receipt["session_id"])
    session.pop("learning_workspace_id")
    session_store.save(session)
    _rewrite_as_workspace_legacy(tmp_path, task["task_id"])

    restarted = _app(tmp_path, "workspace")
    with TestClient(restarted) as client:
        restored = client.get(f"/api/v1/learning/tasks/{task['task_id']}")
        restored_receipt = client.get(f"/api/v1/learning/tasks/{task['task_id']}/activation")

    assert restored.status_code == 200
    assert restored.json()["workspace_id"] == task["workspace_id"]
    assert restored_receipt.status_code == 200
    assert restored_receipt.json()["version"] == 3
    assert restored_receipt.json()["source_policy_snapshot"]["freshness"] == "all"


def test_real_a6d_v2_active_receipt_and_plan_resume_with_legacy_validation(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path, "workspace")
    with TestClient(app) as client:
        task = _ready_draft(client, topic="学习真实 a6d v2 恢复")
        receipt = _activate(client, task, key="real-a6d-v2")

    record = app.state.learning_activation_service.get(
        owner_id="local",
        workspace_id=task["workspace_id"],
        task_id=task["task_id"],
    )
    legacy_plan = _rewrite_turn_plan_as_a6d_v2(
        tmp_path,
        {**receipt, "task_id": task["task_id"], "kickoff_run_id": record.kickoff_run_id},
    )
    legacy_payload = legacy_plan.to_payload()
    assert "source_policy_revision" not in legacy_payload["retrieval"]
    assert "catalog_revision" not in legacy_payload["resume"]
    assert "source_policy_revision" not in legacy_payload["resume"]

    session_store = CodingSessionStore(tmp_path / ".coding" / "sessions")
    session = session_store.load(receipt["session_id"])
    session.pop("learning_workspace_id")
    session_store.save(session)
    _rewrite_as_workspace_legacy(tmp_path, task["task_id"])

    restarted = _app(tmp_path, "workspace")
    with TestClient(restarted) as client:
        restored = client.get(f"/api/v1/learning/tasks/{task['task_id']}/activation")
        resumed = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/resume",
            json={"expected_revision": 1},
        )

    assert restored.status_code == 200, restored.json()
    assert restored.json()["resume_validation_version"] == "legacy_l0_v2"
    assert resumed.status_code == 200, resumed.json()
    assert resumed.json()["turn_context_plan_hash"] == legacy_plan.plan_hash


def test_a6d_v2_legacy_missing_original_catalog_binding_stays_blocked(tmp_path: Path) -> None:
    app = _app(tmp_path, "workspace")
    with TestClient(app) as client:
        task = _ready_draft(client, topic="学习 legacy catalog fail closed")
        receipt = _activate(client, task, key="legacy-missing-catalog")

    record = app.state.learning_activation_service.get(
        owner_id="local", workspace_id=task["workspace_id"], task_id=task["task_id"]
    )
    _rewrite_turn_plan_as_a6d_v2(
        tmp_path,
        {**receipt, "task_id": task["task_id"], "kickoff_run_id": record.kickoff_run_id},
    )
    session_store = CodingSessionStore(tmp_path / ".coding" / "sessions")
    session = session_store.load(receipt["session_id"])
    session.pop("learning_workspace_id")
    session_store.save(session)
    _rewrite_as_workspace_legacy(tmp_path, task["task_id"])

    database = tmp_path / ".coding" / "learning-tasks.sqlite3"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT receipt_json FROM learning_task_activations WHERE task_id = ?",
            (task["task_id"],),
        ).fetchone()
        assert row is not None
        legacy_receipt = json.loads(str(row[0]))
        legacy_receipt.pop("catalog_revision")
        connection.execute(
            "UPDATE learning_task_activations SET receipt_json = ? WHERE task_id = ?",
            (json.dumps(legacy_receipt, sort_keys=True), task["task_id"]),
        )
        connection.commit()

    restarted = _app(tmp_path, "workspace")
    with TestClient(restarted) as client:
        restored = client.get(f"/api/v1/learning/tasks/{task['task_id']}")

    assert restored.status_code == 404
    with sqlite3.connect(database) as connection:
        status = connection.execute(
            "SELECT status FROM learning_tasks WHERE task_id = ?", (task["task_id"],)
        ).fetchone()
    assert status == ("blocked",)


@pytest.mark.parametrize("field", ("learning_plan_id", "learning_plan_hash", "dag_hash"))
def test_resume_rejects_task_side_future_identity(tmp_path: Path, field: str) -> None:
    app = _app(tmp_path, "workspace")
    with TestClient(app) as client:
        task = _ready_draft(client, topic=f"学习 Task identity {field}")
        _activate(client, task, key=f"task-identity-{field}")
        _tamper_task_identity(tmp_path, task["task_id"], field)

        resumed = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/resume",
            json={"expected_revision": 1},
        )

    assert resumed.status_code == 409
    assert resumed.json()["detail"]["code"] == "learning_resume_validation_failed"


@pytest.mark.parametrize("field", ("learning_plan_id", "learning_plan_hash", "dag_hash"))
def test_activate_rejects_task_side_future_identity(tmp_path: Path, field: str) -> None:
    app = _app(tmp_path, "workspace")
    with TestClient(app) as client:
        task = _ready_draft(client, topic=f"学习激活 Task identity {field}")
        _tamper_task_identity(tmp_path, task["task_id"], field)

        activated = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/activate",
            headers={"Idempotency-Key": f"activate-task-identity-{field}"},
            json={"expected_revision": 1},
        )

    assert activated.status_code == 409
    assert activated.json()["detail"]["code"] == "learning_activation_contract_conflict"


@pytest.mark.parametrize("field", ("learning_plan_id", "learning_plan_hash", "dag_hash"))
def test_reconciliation_rejects_task_side_future_identity(tmp_path: Path, field: str) -> None:
    root = tmp_path / field
    root.mkdir()
    app = _app(root, "workspace")

    def fail_before_receipt(point: str, _activation) -> None:  # type: ignore[no-untyped-def]
        if point == "before_receipt":
            raise RuntimeError("injected before receipt")

    app.state.learning_activation_service.failure_injector = fail_before_receipt
    with TestClient(app) as client:
        task = _ready_draft(client, topic=f"学习 reconciliation Task identity {field}")
        failed = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/activate",
            headers={"Idempotency-Key": f"reconcile-task-identity-{field}"},
            json={"expected_revision": 1},
        )
        assert failed.status_code == 503

    _tamper_task_identity(root, task["task_id"], field)
    restarted = _app(root, "workspace")
    with TestClient(restarted) as client:
        receipt = client.get(f"/api/v1/learning/tasks/{task['task_id']}/activation")

    assert receipt.status_code == 200
    assert receipt.json()["receipt_status"] == "activation_failed"
    assert receipt.json()["failure_code"] == "learning_activation_contract_conflict"


def test_startup_keeps_unverifiable_workspace_legacy_invisible_and_blocked(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path, "workspace")
    with TestClient(app) as client:
        draft = _ready_draft(client, topic="legacy draft")
        active = _ready_draft(client, topic="legacy active without plan")
        receipt = _activate(client, active, key="legacy-no-plan")

    record = app.state.learning_activation_service.get(
        owner_id="local",
        workspace_id=active["workspace_id"],
        task_id=active["task_id"],
    )

    _rewrite_as_workspace_legacy(tmp_path, draft["task_id"])
    _rewrite_as_workspace_legacy(tmp_path, active["task_id"])
    plan_store = TurnPlanStore(tmp_path / ".coding", receipt["session_id"])
    with sqlite3.connect(plan_store.path) as connection:
        connection.execute(
            "DELETE FROM turn_context_plans WHERE run_id = ?", (record.kickoff_run_id,)
        )
        connection.commit()

    restarted = _app(tmp_path, "workspace")
    with TestClient(restarted) as client:
        listed = client.get("/api/v1/learning/tasks")
        draft_read = client.get(f"/api/v1/learning/tasks/{draft['task_id']}")
        active_read = client.get(f"/api/v1/learning/tasks/{active['task_id']}")

    assert listed.json() == []
    assert draft_read.status_code == 404
    assert active_read.status_code == 404
    with sqlite3.connect(tmp_path / ".coding" / "learning-tasks.sqlite3") as connection:
        statuses = dict(
            connection.execute(
                "SELECT task_id, status FROM learning_tasks ORDER BY task_id"
            ).fetchall()
        )
    assert statuses[draft["task_id"]] == "blocked"
    assert statuses[active["task_id"]] == "blocked"


def test_reconciliation_rejects_source_policy_drift_before_replaying_resources(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path, "workspace")

    def fail_before_receipt(point: str, _activation) -> None:  # type: ignore[no-untyped-def]
        if point == "before_receipt":
            raise RuntimeError("injected before receipt")

    app.state.learning_activation_service.failure_injector = fail_before_receipt
    with TestClient(app) as client:
        task = _ready_draft(client, topic="学习 reconciliation policy")
        failed = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/activate",
            headers={"Idempotency-Key": "reconcile-policy"},
            json={"expected_revision": 1},
        )
        assert failed.status_code == 503

    database = tmp_path / ".coding" / "learning-tasks.sqlite3"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT payload_json FROM learning_tasks WHERE task_id = ?", (task["task_id"],)
        ).fetchone()
        assert row is not None
        payload = json.loads(str(row[0]))
        payload["source_policy"]["domains"] = ["example.com"]
        connection.execute(
            "UPDATE learning_tasks SET payload_json = ? WHERE task_id = ?",
            (json.dumps(payload, sort_keys=True), task["task_id"]),
        )
        connection.commit()

    restarted = _app(tmp_path, "workspace")
    with TestClient(restarted) as client:
        receipt = client.get(f"/api/v1/learning/tasks/{task['task_id']}/activation").json()

    assert receipt["receipt_status"] == "activation_failed"
    assert receipt["failure_code"] == "learning_activation_source_policy_conflict"
