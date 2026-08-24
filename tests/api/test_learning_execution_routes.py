from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sage_harness import KnowledgeEvidence, KnowledgeRetrievalResult

from api import learning as learning_api
from api.main import create_app
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

        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-store"
        assert replay.json() == first.json()
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "learning_resume_checkpoint_conflict"
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
        assert artifact.json()["citations"][0]["evidence_ref"] == "kcite-api-1"


def test_learning_l3_openapi_declares_browser_contracts(tmp_path: Path) -> None:
    schema = _app(tmp_path).openapi()
    paths = schema["paths"]
    base = "/api/v1/learning/tasks/{task_id}"
    assert "post" in paths[f"{base}/advance"]
    assert "get" in paths[f"{base}/resume"]
    assert "get" in paths[f"{base}/artifacts/{{artifact_id}}"]
    for status_code in ("409", "503"):
        response_schema = paths[f"{base}/advance"]["post"]["responses"][status_code]["content"][
            "application/json"
        ]["schema"]
        assert response_schema["$ref"].endswith("/LearningErrorResponse")
