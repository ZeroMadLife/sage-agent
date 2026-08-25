from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sage_harness import KnowledgeEvidence, KnowledgeRetrievalResult

from api import coding as coding_api
from api import learning as learning_api
from api.coding import CodingRuntimeRehydrateError
from api.main import create_app
from api.schemas import LearningErrorResponse
from core.learning import LearningExecutionContext, LearningExecutionService, LearningMapService


class FakeKnowledgePort:
    workspace_id = "knowledge-workspace"
    available = True

    async def search(
        self,
        query: str,
        *,
        workspace_id: str,
        token_budget: int,
        top_k: int = 8,
    ) -> KnowledgeRetrievalResult:
        return KnowledgeRetrievalResult(
            query=query,
            workspace_id=workspace_id,
            status="evidence_found",
            token_budget=token_budget,
            used_tokens=50,
            omitted_count=0,
            evidence=(
                KnowledgeEvidence(
                    citation_id="kcite-api-1",
                    content="Large evidence body must stay outside Resume.",
                    page_revision="page-api-r1",
                    source_revision="source-api-r1",
                    metadata={"title": "Checkpoint API guide"},
                ),
            ),
        )


def _app(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return create_app(
        coding_model_factory=lambda: object(),
        coding_workspace_root=workspace,
        coding_storage_root=tmp_path / ".coding",
        coding_default_runtime_profile="legacy",
    )


def _active_task(client: TestClient) -> dict[str, object]:
    task = client.post(
        "/api/v1/learning/tasks/draft",
        json={
            "topic": "学习 checkpoint API",
            "desired_outcome": "能够解释恢复接口",
            "starting_level": "beginner",
            "time_budget_minutes_per_week": 120,
        },
    ).json()
    response = client.post(
        f"/api/v1/learning/tasks/{task['task_id']}/activate",
        headers={"Idempotency-Key": "activate-api-r1"},
        json={"expected_revision": 1},
    )
    assert response.status_code == 200
    return client.get(f"/api/v1/learning/tasks/{task['task_id']}").json()


def test_advance_resume_and_scoped_artifact_get(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = _app(tmp_path)
    context = LearningExecutionContext(
        thread_id="session-api",
        parent_run_id="run-api",
        workspace_path=str(tmp_path / "workspace"),
        capability_revision="test-capability-revision",
        catalog_revision="test-catalog-revision",
        allowed_capabilities=frozenset(),
        remaining_token_budget=2_000,
    )

    async def fake_execution_service(request, **kwargs):  # type: ignore[no-untyped-def]
        scope = await learning_api.asyncio.to_thread(
            request.app.state.learning_readonly_scope_resolver.resolve,
            owner_id=kwargs["owner_id"],
            workspace_id=kwargs["workspace_id"],
            task_id=kwargs["task"].task_id,
        )
        bound_context = LearningExecutionContext(
            thread_id=scope.session_id,
            parent_run_id="run-api",
            workspace_path=context.workspace_path,
            capability_revision=scope.capability_revision,
            catalog_revision=scope.catalog_revision,
            allowed_capabilities=frozenset(scope.allowed_capabilities),
            remaining_token_budget=2_000,
        )
        return (
            LearningExecutionService(
                store=request.app.state.learning_artifact_store,
                map_service=LearningMapService(knowledge_port=FakeKnowledgePort()),
            ),
            bound_context,
        )

    monkeypatch.setattr(learning_api, "_execution_service", fake_execution_service)
    with TestClient(app) as client:
        task = _active_task(client)
        url = f"/api/v1/learning/tasks/{task['task_id']}/advance"
        first = client.post(
            url,
            headers={"Idempotency-Key": "advance-api-1"},
            json={"expected_checkpoint_revision": 0},
        )
        replay = client.post(
            url,
            headers={"Idempotency-Key": "advance-api-1"},
            json={"expected_checkpoint_revision": 0},
        )
        stale = client.post(
            url,
            headers={"Idempotency-Key": "advance-api-stale"},
            json={"expected_checkpoint_revision": 0},
        )

        assert first.status_code == 200, first.text
        assert first.headers["cache-control"] == "no-store"
        assert replay.json() == first.json()
        assert stale.status_code == 409
        assert LearningErrorResponse.model_validate(stale.json()).detail.code == (
            "learning_resume_checkpoint_conflict"
        )
        assert "Large evidence body" not in first.text

        resume = client.get(f"/api/v1/learning/tasks/{task['task_id']}/resume")
        artifact_id = first.json()["artifact"]["artifact_id"]
        artifact = client.get(f"/api/v1/learning/tasks/{task['task_id']}/artifacts/{artifact_id}")
        assert resume.status_code == 200
        assert resume.headers["cache-control"] == "no-store"
        assert resume.json() == first.json()
        assert artifact.status_code == 200
        assert artifact.headers["cache-control"] == "no-store"
        assert "Large evidence body" not in artifact.json()["content"]
        assert artifact.json()["schema_version"] == 1
        assert artifact.json()["goal_id"] == task["learning_goal_ref"]["goal_id"]
        assert artifact.json()["plan_revision"] == 1
        assert len(artifact.json()["unit_ids"]) == 1
        assert artifact.json()["research_receipt_ref"] == ""
        assert artifact.json()["citations"][0]["evidence_ref"] == "kcite-api-1"


def test_learning_l3_openapi_declares_browser_contracts(tmp_path: Path) -> None:
    schema = _app(tmp_path).openapi()
    paths = schema["paths"]
    base = "/api/v1/learning/tasks/{task_id}"
    assert "post" in paths[f"{base}/advance"]
    assert "get" in paths[f"{base}/resume"]
    assert "get" in paths[f"{base}/artifacts/{{artifact_id}}"]
    operations = (
        paths[f"{base}/advance"]["post"],
        paths[f"{base}/resume"]["get"],
        paths[f"{base}/artifacts/{{artifact_id}}"]["get"],
    )
    for operation in operations:
        for status_code in ("404", "409", "422", "503"):
            response_schema = operation["responses"][status_code]["content"]["application/json"][
                "schema"
            ]
            assert response_schema["$ref"].endswith("/LearningErrorResponse")
    error_detail = schema["components"]["schemas"]["LearningErrorDetail"]
    code_schema = error_detail["properties"]["code"]
    assert code_schema["$ref"].endswith("/LearningFailureCode")


def test_missing_and_invalid_learning_task_ids_return_structured_errors(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        missing = client.get(f"/api/v1/learning/tasks/ltask_{'0' * 32}/resume")
        invalid = client.get("/api/v1/learning/tasks/not-a-learning-task/resume")

    assert missing.status_code == 404
    assert LearningErrorResponse.model_validate(missing.json()).detail.code == (
        "learning_task_not_found"
    )
    assert invalid.status_code == 422
    assert LearningErrorResponse.model_validate(invalid.json()).detail.code == (
        "learning_task_invalid_id"
    )


@pytest.mark.parametrize(
    "task_id",
    (
        "ltask_",
        f"ltask_{'a' * 31}",
        f"ltask_{'a' * 33}",
        f"ltask_{'A' * 32}",
        f"ltask_{'a' * 31}!",
    ),
)
def test_noncanonical_task_ids_fail_before_store_access(tmp_path: Path, task_id: str) -> None:
    app = _app(tmp_path)
    app.state.learning_task_service = None
    with TestClient(app) as client:
        response = client.get(f"/api/v1/learning/tasks/{task_id}/resume")

    assert response.status_code == 422
    assert LearningErrorResponse.model_validate(response.json()).detail.code == (
        "learning_task_invalid_id"
    )


@pytest.mark.parametrize(
    "artifact_id",
    (
        "lart_",
        f"lart_{'a' * 23}",
        f"lart_{'a' * 25}",
        f"lart_{'A' * 24}",
        f"lart_{'a' * 23}!",
    ),
)
def test_noncanonical_artifact_ids_fail_before_store_access(
    tmp_path: Path, artifact_id: str
) -> None:
    app = _app(tmp_path)
    app.state.learning_readonly_scope_resolver = None
    task_id = f"ltask_{'0' * 32}"
    with TestClient(app) as client:
        response = client.get(f"/api/v1/learning/tasks/{task_id}/artifacts/{artifact_id}")

    assert response.status_code == 422
    assert LearningErrorResponse.model_validate(response.json()).detail.code == (
        "learning_request_invalid"
    )


def test_request_validation_and_execution_helpers_return_structured_errors(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        task = _active_task(client)
        invalid = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/advance",
            headers={"Idempotency-Key": "invalid-revision"},
            json={"expected_checkpoint_revision": -1},
        )
        app.state.learning_artifact_store = None
        unavailable = client.get(f"/api/v1/learning/tasks/{task['task_id']}/resume")

    assert invalid.status_code == 422
    assert LearningErrorResponse.model_validate(invalid.json()).detail.code == (
        "learning_request_invalid"
    )
    assert unavailable.status_code == 503
    assert LearningErrorResponse.model_validate(unavailable.json()).detail.code == (
        "learning_artifact_store_unavailable"
    )


def test_runtime_rehydrate_failure_is_closed_structured_503(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    app = _app(tmp_path)

    async def fail_rehydrate(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise CodingRuntimeRehydrateError

    monkeypatch.setattr(coding_api, "_rehydrate_coding_runtime", fail_rehydrate)
    with TestClient(app, raise_server_exceptions=False) as client:
        task = _active_task(client)
        kickoff = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/kickoff",
            headers={"Idempotency-Key": "rehydrate-kickoff"},
            json={"expected_revision": task["task_revision"]},
        )
        assert kickoff.status_code == 200, kickoff.text
        response = client.post(
            f"/api/v1/learning/tasks/{task['task_id']}/advance",
            headers={"Idempotency-Key": "rehydrate-advance"},
            json={"expected_checkpoint_revision": 0},
        )

    assert response.status_code == 503
    assert LearningErrorResponse.model_validate(response.json()).detail.code == (
        "learning_runtime_rehydrate_failed"
    )


def test_first_advance_without_research_profile_returns_canonical_source_gap(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        draft = client.post(
            "/api/v1/learning/tasks/draft",
            json={
                "topic": "学习离线恢复边界",
                "desired_outcome": "识别当前来源缺口",
                "starting_level": "beginner",
                "time_budget_minutes_per_week": 120,
                "source_policy": {
                    "knowledge": "disabled",
                    "web": "allowed_when_insufficient",
                    "domains": [],
                    "freshness": "all",
                },
            },
        ).json()
        activated = client.post(
            f"/api/v1/learning/tasks/{draft['task_id']}/activate",
            headers={"Idempotency-Key": "activate-no-provider"},
            json={"expected_revision": 1},
        )
        assert activated.status_code == 200
        kickoff = client.post(
            f"/api/v1/learning/tasks/{draft['task_id']}/kickoff",
            headers={"Idempotency-Key": "kickoff-no-provider"},
            json={"expected_revision": 1},
        )
        assert kickoff.status_code == 200, kickoff.text
        task = client.get(f"/api/v1/learning/tasks/{draft['task_id']}").json()
        url = f"/api/v1/learning/tasks/{task['task_id']}/advance"

        first = client.post(
            url,
            headers={"Idempotency-Key": "advance-no-provider-1"},
            json={"expected_checkpoint_revision": 0},
        )
        assert first.status_code == 200, first.text
        assert first.json()["stage"] == "knowledge_pending"
        assert first.json()["artifact"]["status"] == "source_gap"

        second = client.post(
            url,
            headers={"Idempotency-Key": "advance-no-provider-2"},
            json={"expected_checkpoint_revision": first.json()["checkpoint_revision"]},
        )
        assert second.status_code == 200
        assert second.json()["stage"] == "source_gap"
        assert second.json()["next_action"] == "research"
