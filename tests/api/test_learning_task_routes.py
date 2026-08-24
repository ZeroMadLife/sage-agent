from pathlib import Path

from fastapi.testclient import TestClient

from api.main import create_app


def _app(tmp_path: Path, *, app_env: str = "development"):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return create_app(
        coding_workspace_root=workspace,
        coding_storage_root=tmp_path / ".coding",
        cloud_app_env=app_env,
    )


def test_create_read_and_patch_learning_draft_without_starting_runtime(tmp_path: Path) -> None:
    model_calls = 0

    def forbidden_model_factory(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal model_calls
        model_calls += 1
        raise AssertionError("draft must not create a model")

    app = _app(tmp_path)
    app.state.coding_model_factory = forbidden_model_factory
    with TestClient(app) as client:
        created_response = client.post(
            "/api/v1/learning/tasks/draft",
            json={"topic": "我想学阳明心学，不要联网"},
        )

        assert created_response.status_code == 201
        assert created_response.headers["cache-control"] == "no-store"
        created = created_response.json()
        assert created["version"] == 1
        assert created["task_id"].startswith("ltask_")
        assert created["task_revision"] == 1
        assert created["learning_plan_id"] is None
        assert created["learning_plan_hash"] is None
        assert created["source_policy"] == {
            "knowledge": "preferred",
            "web": "forbidden",
            "domains": [],
            "freshness": "all",
        }
        assert created["clarification"]["required_fields"] == [
            "desired_outcome",
            "starting_level",
            "time_budget_minutes_per_week",
        ]

        task_id = created["task_id"]
        patched_response = client.patch(
            f"/api/v1/learning/tasks/{task_id}",
            json={
                "expected_revision": 1,
                "desired_outcome": "能够解释核心概念及主要争议",
                "starting_level": "beginner",
                "time_budget_minutes_per_week": 240,
            },
        )
        assert patched_response.status_code == 200
        patched = patched_response.json()
        assert patched["task_revision"] == 2
        assert patched["clarification"]["required_fields"] == []

        read_response = client.get(f"/api/v1/learning/tasks/{task_id}")
        assert read_response.status_code == 200
        assert read_response.headers["cache-control"] == "no-store"
        assert read_response.json() == patched

        stale = client.patch(
            f"/api/v1/learning/tasks/{task_id}",
            json={"expected_revision": 1, "desired_outcome": "stale"},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"] == {
            "code": "learning_task_revision_conflict",
            "current_revision": 2,
        }

    assert model_calls == 0
    assert app.state.coding_sessions == {}
    session_root = tmp_path / ".coding" / "sessions"
    assert not session_root.exists() or list(session_root.glob("*.json")) == []
    assert not (tmp_path / ".coding" / "mastery-ledger.sqlite3").exists()


def test_learning_draft_rejects_invalid_input_and_unknown_task(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        assert client.post("/api/v1/learning/tasks/draft", json={"topic": "   "}).status_code == 422
        assert client.get("/api/v1/learning/tasks/ltask_missing").status_code == 404

        created = client.post(
            "/api/v1/learning/tasks/draft", json={"topic": "学习 Java 并发"}
        ).json()
        empty_patch = client.patch(
            f"/api/v1/learning/tasks/{created['task_id']}",
            json={"expected_revision": 1},
        )
        assert empty_patch.status_code == 422
        assert (
            client.get(f"/api/v1/learning/tasks/{created['task_id']}").json()["task_revision"] == 1
        )


def test_learning_draft_requires_authentication_in_production(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path, app_env="production")) as client:
        response = client.post(
            "/api/v1/learning/tasks/draft",
            json={"topic": "学习 Java 并发"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "cloud authentication is required"


def test_learning_openapi_keeps_a1_draft_contract_and_adds_a2_activation(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        openapi = client.get("/openapi.json").json()
        paths = openapi["paths"]

    assert set(paths["/api/v1/learning/tasks/draft"]) == {"post"}
    assert set(paths["/api/v1/learning/tasks/{task_id}"]) == {"get", "patch"}
    assert set(paths["/api/v1/learning/tasks/{task_id}/activate"]) == {"post"}
    assert set(paths["/api/v1/learning/tasks/{task_id}/activation"]) == {"get"}
    assert set(paths["/api/v1/learning/tasks/{task_id}/resume"]) == {"post"}
    activation = openapi["components"]["schemas"]["LearningActivationResponse"]
    properties = activation["properties"]
    assert properties["learning_plan_id"]["anyOf"][0]["type"] == "string"
    assert properties["learning_plan_hash"]["anyOf"][0]["type"] == "string"
    assert properties["turn_context_plan_id"]["type"] == "string"
    assert properties["turn_context_plan_hash"]["anyOf"][0]["type"] == "string"
    assert properties["dag_hash"]["anyOf"][0]["type"] == "string"
    assert properties["plan_id"]["deprecated"] is True
    assert properties["plan_hash"]["deprecated"] is True
