"""Run-frozen Chat Harness surface context contracts."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from api.harness_context import validate_surface_context
from api.main import create_app
from api.schemas import HarnessSurfaceContext
from core.cloud.auth.repository import CloudRepository
from core.cloud.model_providers import ModelProviderRepository
from core.knowledge import KnowledgeSourceRoot
from core.knowledge.jobs import KnowledgeJobService
from db.database import create_engine, create_session_factory
from db.migrations import init_db


class FinalModel:
    prompts: ClassVar[list[str]] = []

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return "<final>完成。</final>"


def _receive_until_terminal(websocket) -> list[dict]:
    events: list[dict] = []
    while True:
        event = websocket.receive_json()
        events.append(event)
        if event["kind"] == "terminal":
            return events


def _coding_app(tmp_path: Path):
    FinalModel.prompts.clear()
    workspace = tmp_path / "coding"
    workspace.mkdir()
    (workspace / "README.md").write_text("# Sage\n", encoding="utf-8")
    return create_app(
        coding_model_factory=FinalModel,
        coding_workspace_root=workspace,
        coding_storage_root=tmp_path / ".coding",
        cloud_app_env="development",
    )


def _knowledge_app(tmp_path: Path):
    coding = tmp_path / "coding"
    coding.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=knowledge,
        check=True,
        capture_output=True,
        text=True,
    )
    app = create_app(
        coding_model_factory=FinalModel,
        coding_workspace_root=coding,
        coding_storage_root=tmp_path / ".coding",
        cloud_app_env="development",
        knowledge_workspace_root=knowledge,
        knowledge_database_path=knowledge / ".sage" / "knowledge.sqlite3",
        knowledge_source_roots={
            "learning": KnowledgeSourceRoot(
                root_id="learning",
                kind="obsidian",
                label="Learning",
                path=vault,
            )
        },
        knowledge_jobs_enabled=False,
    )
    return app, vault


@pytest.fixture
async def cloud_repositories() -> AsyncIterator[
    tuple[CloudRepository, ModelProviderRepository]
]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    factory = create_session_factory(engine)
    await init_db(engine)
    try:
        yield (
            CloudRepository(factory),
            ModelProviderRepository(
                factory,
                encryption_secret="test-provider-secret-" * 4,
            ),
        )
    finally:
        await engine.dispose()


def test_coding_context_is_canonicalized_frozen_and_replayable(tmp_path: Path) -> None:
    app = _coding_app(tmp_path)
    with TestClient(app) as client:
        created = client.post("/api/v1/coding/session", json={}).json()
        context = {
            "surface": "coding",
            "workspace_id": created["workspace_id"],
            "resource": {
                "type": "coding_workspace",
                "id": created["workspace_id"],
                "label": "forged workspace label",
            },
            "selection": {
                "type": "coding_file",
                "id": "README.md",
                "label": "forged file label",
            },
            "operation_refs": [],
        }
        with client.websocket_connect(
            f"/api/v1/coding/{created['session_id']}/stream"
        ) as websocket:
            websocket.send_json({"content": "解释文件", "surface_context": context})
            streamed = _receive_until_terminal(websocket)
        replayed = client.get(
            f"/api/v1/coding/session/{created['session_id']}/timeline"
        ).json()["items"]

    started = next(item for item in streamed if item["payload"].get("event") == "run_started")
    assert started["payload"]["surface_context"] == {
        "surface": "coding",
        "workspace_id": created["workspace_id"],
        "resource": {
            "type": "coding_workspace",
            "id": created["workspace_id"],
            "label": "coding",
        },
        "selection": {
            "type": "coding_file",
            "id": "README.md",
            "label": "README.md",
        },
        "operation_refs": [],
    }
    assert next(
        item for item in replayed if item["event_id"] == started["event_id"]
    ) == started
    assert len(FinalModel.prompts) == 1
    assert '"label":"coding"' in FinalModel.prompts[0]
    assert '"label":"README.md"' in FinalModel.prompts[0]
    assert "forged workspace label" not in FinalModel.prompts[0]
    assert "forged file label" not in FinalModel.prompts[0]


def test_forged_coding_workspace_and_escaping_file_are_rejected_before_model(
    tmp_path: Path,
) -> None:
    app = _coding_app(tmp_path)
    with TestClient(app) as client:
        created = client.post("/api/v1/coding/session", json={}).json()
        base = {
            "surface": "coding",
            "workspace_id": created["workspace_id"],
            "resource": {"type": "coding_workspace", "id": created["workspace_id"]},
            "selection": None,
            "operation_refs": [],
        }
        with client.websocket_connect(
            f"/api/v1/coding/{created['session_id']}/stream"
        ) as websocket:
            websocket.send_json(
                {"content": "forged", "surface_context": {**base, "workspace_id": "other"}}
            )
            forged = _receive_until_terminal(websocket)
        with client.websocket_connect(
            f"/api/v1/coding/{created['session_id']}/stream"
        ) as websocket:
            websocket.send_json(
                {
                    "content": "escape",
                    "surface_context": {
                        **base,
                        "selection": {"type": "coding_file", "id": "../secret.txt"},
                    },
                }
            )
            escaped = _receive_until_terminal(websocket)

    for events in (forged, escaped):
        assert not any(item["payload"].get("event") == "run_started" for item in events)
        assert not any(item["kind"] == "model" for item in events)
        assert events[-1]["status"] == "error"


def test_knowledge_page_and_graph_revision_are_verified_and_frozen(tmp_path: Path) -> None:
    app, vault = _knowledge_app(tmp_path)
    note = vault / "harness.md"
    note.write_text("# Agent Harness\n\n可恢复、可审核。\n", encoding="utf-8")
    store = app.state.knowledge_store
    proposal = store.ingest("learning", "harness.md")
    applied = store.evaluate_and_apply_policy(proposal.proposal_id)
    assert applied.projection_status == "complete"
    page = store.list_pages()[0]
    graph = store.graph_overview()
    node = next(item for item in graph.nodes if item.page_id == page.page_id)

    with TestClient(app) as client:
        session = client.post("/api/v1/coding/session", json={}).json()
        context = {
            "surface": "knowledge",
            "workspace_id": graph.snapshot.workspace_id,
            "resource": {
                "type": "knowledge_page",
                "id": page.page_id,
                "revision": page.current_revision,
            },
            "selection": {
                "type": "graph_node",
                "id": node.node_id,
                "revision": node.page_revision,
            },
            "graph_revision": graph.snapshot.graph_revision,
            "operation_refs": [],
        }
        with client.websocket_connect(
            f"/api/v1/coding/{session['session_id']}/stream"
        ) as websocket:
            websocket.send_json({"content": "解释节点", "surface_context": context})
            events = _receive_until_terminal(websocket)

    started = next(item for item in events if item["payload"].get("event") == "run_started")
    frozen = started["payload"]["surface_context"]
    assert frozen["workspace_id"] == "knowledge-local"
    assert frozen["resource"] == {
        "type": "knowledge_page",
        "id": page.page_id,
        "revision": page.current_revision,
        "label": "Agent Harness",
    }
    assert frozen["selection"]["id"] == node.node_id
    assert frozen["selection"]["revision"] == node.page_revision
    assert frozen["graph_revision"] == graph.snapshot.graph_revision


def test_stale_knowledge_revision_is_rejected_before_model(tmp_path: Path) -> None:
    app, vault = _knowledge_app(tmp_path)
    (vault / "harness.md").write_text("# Agent Harness\n", encoding="utf-8")
    store = app.state.knowledge_store
    proposal = store.ingest("learning", "harness.md")
    store.evaluate_and_apply_policy(proposal.proposal_id)
    page = store.list_pages()[0]

    with TestClient(app) as client:
        session = client.post("/api/v1/coding/session", json={}).json()
        with client.websocket_connect(
            f"/api/v1/coding/{session['session_id']}/stream"
        ) as websocket:
            websocket.send_json(
                {
                    "content": "解释旧页面",
                    "surface_context": {
                        "surface": "knowledge",
                        "workspace_id": "knowledge-local",
                        "resource": {
                            "type": "knowledge_page",
                            "id": page.page_id,
                            "revision": "stale-revision",
                        },
                        "selection": None,
                        "operation_refs": [],
                    },
                }
            )
            events = _receive_until_terminal(websocket)

    assert not any(item["payload"].get("event") == "run_started" for item in events)
    assert not any(item["kind"] == "model" for item in events)
    assert events[-1]["status"] == "error"


def test_knowledge_job_reference_is_verified_against_its_workspace(tmp_path: Path) -> None:
    app, _vault = _knowledge_app(tmp_path)

    class Repository:
        async def get_job(self, job_id: str):
            return SimpleNamespace(job_id=job_id, workspace_id="knowledge-local")

    service = object.__new__(KnowledgeJobService)
    service.repository = Repository()
    context = HarnessSurfaceContext(
        surface="knowledge",
        workspace_id="knowledge-local",
        operation_refs=[{"kind": "knowledge_job", "id": "job-1"}],
    )
    runtime = app.state.coding_sessions.get("missing")
    assert runtime is None
    session_id = TestClient(app).post("/api/v1/coding/session", json={}).json()["session_id"]
    runtime = app.state.coding_sessions[session_id]

    canonical = asyncio.run(
        validate_surface_context(
            context,
            runtime=runtime,
            knowledge_store=app.state.knowledge_store,
            knowledge_job_service=service,
            app_env="development",
        )
    )

    assert [item.model_dump() for item in canonical.operation_refs] == [
        {"kind": "knowledge_job", "id": "job-1"}
    ]


async def test_coding_stream_rejects_a_session_from_another_owner(
    tmp_path: Path,
    cloud_repositories: tuple[CloudRepository, ModelProviderRepository],
) -> None:
    cloud_repository, model_provider_repository = cloud_repositories
    await cloud_repository.create_invite("owner-invite", email="owner@example.com")
    await cloud_repository.create_invite("other-invite", email="other@example.com")
    workspace = tmp_path / "coding"
    workspace.mkdir()
    app_kwargs = {
        "cloud_repository": cloud_repository,
        "cloud_model_provider_repository": model_provider_repository,
        "cloud_dev_login_enabled": True,
        "cloud_app_env": "development",
        "coding_model_factory": FinalModel,
        "coding_workspace_root": workspace,
        "coding_storage_root": tmp_path / ".coding",
    }
    owner = TestClient(create_app(**app_kwargs))
    other = TestClient(create_app(**app_kwargs))
    assert owner.post(
        "/api/v1/cloud/auth/dev/login",
        json={"email": "owner@example.com", "display_name": "Owner", "invite_code": "owner-invite"},
    ).status_code == 200
    assert other.post(
        "/api/v1/cloud/auth/dev/login",
        json={"email": "other@example.com", "display_name": "Other", "invite_code": "other-invite"},
    ).status_code == 200
    session = owner.post("/api/v1/coding/session", json={}).json()

    with pytest.raises(WebSocketDenialResponse) as denied, other.websocket_connect(
        f"/api/v1/coding/{session['session_id']}/stream"
    ):
        pass

    assert denied.value.status_code == 404
