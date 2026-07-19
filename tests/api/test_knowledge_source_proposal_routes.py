"""Coding-thread source proposal review API contracts."""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
from pathlib import Path
from typing import Any

import fakeredis.aioredis
from fastapi.testclient import TestClient

from api.main import create_app
from core.coding.persistence.tool_result_store import ToolResultStore
from core.harness.knowledge_source_proposal_adapter import (
    CodingKnowledgeSourceProposalService,
)
from core.knowledge import KnowledgeStore
from core.knowledge.jobs import (
    KnowledgeJobRepository,
    KnowledgeJobService,
    RedisKnowledgeJobQueue,
)
from core.knowledge.source_proposals import KnowledgeSourceProposalRepository
from db.database import create_engine, create_session_factory
from db.migrations import init_db


class FakeModel:
    def __init__(self, *args: object, **kwargs: object) -> None:
        _ = args, kwargs


def _app(tmp_path: Path) -> tuple[Any, Any, Any]:
    coding = tmp_path / "coding"
    coding.mkdir()
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=knowledge,
        check=True,
        capture_output=True,
        text=True,
    )
    store = KnowledgeStore(knowledge, knowledge / ".sage" / "knowledge.sqlite3", {})
    store.initialize()
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'control.sqlite3'}")
    asyncio.run(init_db(engine))
    factory = create_session_factory(engine)
    redis = fakeredis.aioredis.FakeRedis()
    jobs = KnowledgeJobService(
        store,
        KnowledgeJobRepository(factory),
        RedisKnowledgeJobQueue(redis, stream=f"api-proposal:{tmp_path.name}"),
        poll_seconds=0.01,
        retry_base_seconds=0,
    )
    proposals = CodingKnowledgeSourceProposalService(
        KnowledgeSourceProposalRepository(factory),
        jobs,
        coding_storage_root=tmp_path / ".coding",
    )
    app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=coding,
        coding_storage_root=tmp_path / ".coding",
        cloud_app_env="development",
        knowledge_workspace_root=knowledge,
        knowledge_job_service=jobs,
        knowledge_source_proposal_service=proposals,
    )
    return app, engine, redis


def _propose(app: Any, session_id: str, run_id: str = "run-web") -> Any:
    content = "Official public evidence"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    receipt = ToolResultStore(app.state.coding_storage_root, session_id, run_id).archive(
        "call-fetch",
        content,
        metadata={
            "artifact_kind": "web_fetch",
            "canonical_url": "https://example.com/official",
            "title": "Official Evidence",
            "retrieved_at": "2026-07-18T09:30:00Z",
            "content_hash": digest,
            "media_type": "text/html",
        },
    )
    return asyncio.run(
        app.state.knowledge_source_proposal_service.propose(
            owner_id="local",
            workspace_id="knowledge-local",
            thread_id=session_id,
            run_id=run_id,
            artifact_ref=receipt.artifact_ref,
            reason="Keep this exact source",
            evidence_refs=("wcite_official",),
        )
    )


def test_source_proposal_routes_list_detail_reject_and_scope(tmp_path: Path) -> None:
    app, engine, redis = _app(tmp_path)
    with TestClient(app) as client:
        first = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        second = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        proposal = _propose(app, first)

        listed = client.get(f"/api/v1/coding/{first}/knowledge/source-proposals")
        detail = client.get(
            f"/api/v1/coding/{first}/knowledge/source-proposals/{proposal.proposal_id}"
        )
        hidden = client.get(
            f"/api/v1/coding/{second}/knowledge/source-proposals/{proposal.proposal_id}"
        )
        rejected = client.post(
            f"/api/v1/coding/{first}/knowledge/source-proposals/{proposal.proposal_id}/reject",
            json={"expected_revision": proposal.revision},
        )
        stale = client.post(
            f"/api/v1/coding/{first}/knowledge/source-proposals/{proposal.proposal_id}/reject",
            json={"expected_revision": proposal.revision},
        )

    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store"
    assert len(listed.json()["proposals"]) == 1
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "no-store"
    assert detail.json()["proposal"]["canonical_url"] == "https://example.com/official"
    assert detail.json()["events"][0]["event_type"] == "proposal_created"
    assert hidden.status_code == 404
    assert rejected.status_code == 200
    assert rejected.headers["cache-control"] == "no-store"
    assert rejected.json()["status"] == "rejected"
    assert stale.status_code == 409
    asyncio.run(redis.aclose())
    asyncio.run(engine.dispose())


def test_source_proposal_approve_returns_job_ref_and_hides_server_path(tmp_path: Path) -> None:
    app, engine, redis = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        proposal = _propose(app, session_id)
        response = client.post(
            f"/api/v1/coding/{session_id}/knowledge/source-proposals/"
            f"{proposal.proposal_id}/approve",
            json={"expected_revision": proposal.revision},
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["status"] == "approved"
    assert payload["job_id"].startswith("kjob_")
    assert payload["target_relative_path"] == f"{proposal.proposal_id}/source.md"
    assert str(tmp_path) not in response.text
    asyncio.run(redis.aclose())
    asyncio.run(engine.dispose())


def test_routes_fail_closed_when_source_proposal_service_is_unavailable(tmp_path: Path) -> None:
    app, engine, redis = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        app.state.knowledge_source_proposal_service = None
        response = client.get(f"/api/v1/coding/{session_id}/knowledge/source-proposals")

    assert response.status_code == 503
    asyncio.run(redis.aclose())
    asyncio.run(engine.dispose())
