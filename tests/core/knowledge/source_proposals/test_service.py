"""End-to-end source proposal lifecycle over private run evidence."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import fakeredis.aioredis
import pytest

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
from core.knowledge.source_proposals import (
    KnowledgeSourceProposalConflictError,
    KnowledgeSourceProposalNotFoundError,
    KnowledgeSourceProposalRepository,
)
from db.database import create_engine, create_session_factory
from db.migrations import init_db


async def _services(tmp_path: Path):  # type: ignore[no-untyped-def]
    knowledge_root = tmp_path / "knowledge"
    knowledge_root.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=knowledge_root,
        check=True,
        capture_output=True,
        text=True,
    )
    store = KnowledgeStore(
        knowledge_root,
        knowledge_root / ".sage" / "knowledge.sqlite3",
        {},
    )
    store.initialize()
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'control.sqlite3'}")
    await init_db(engine)
    factory = create_session_factory(engine)
    redis = fakeredis.aioredis.FakeRedis()
    job_service = KnowledgeJobService(
        store,
        KnowledgeJobRepository(factory),
        RedisKnowledgeJobQueue(redis, stream=f"proposal:{tmp_path.name}"),
        retry_base_seconds=0,
        poll_seconds=0.01,
    )
    proposal_service = CodingKnowledgeSourceProposalService(
        KnowledgeSourceProposalRepository(factory),
        job_service,
        coding_storage_root=tmp_path / ".coding",
    )
    await proposal_service.prepare()
    return proposal_service, job_service, store, engine, redis


def _archive_web_evidence(
    root: Path,
    *,
    thread_id: str = "thread-1",
    run_id: str = "run-1",
    call_id: str = "call-fetch",
    content: str = "Sage verified public evidence",
) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    receipt = ToolResultStore(root, thread_id, run_id).archive(
        call_id,
        content,
        metadata={
            "artifact_kind": "web_fetch",
            "canonical_url": "https://example.com/sage",
            "title": "Sage Evidence",
            "retrieved_at": "2026-07-18T09:30:00Z",
            "content_hash": digest,
            "media_type": "text/html",
            "wire_bytes": len(content.encode("utf-8")),
        },
    )
    return receipt.artifact_ref


async def test_propose_is_idempotent_scoped_and_does_not_ingest_before_approval(
    tmp_path: Path,
) -> None:
    service, _, store, engine, redis = await _services(tmp_path)
    artifact_ref = _archive_web_evidence(tmp_path / ".coding")
    try:
        first = await service.propose(
            owner_id="local",
            workspace_id="knowledge-local",
            thread_id="thread-1",
            run_id="run-1",
            artifact_ref=artifact_ref,
            reason="Save this evidence for the learning goal",
            evidence_refs=("wcite_123",),
        )
        repeated = await service.propose(
            owner_id="local",
            workspace_id="knowledge-local",
            thread_id="thread-1",
            run_id="run-1",
            artifact_ref=artifact_ref,
            reason="Save this evidence for the learning goal",
            evidence_refs=("wcite_123",),
        )

        assert repeated.proposal_id == first.proposal_id
        assert first.status == "pending"
        assert store.list_proposals() == []
        assert "web-evidence" not in store.source_roots
        assert list(service.snapshot_root.iterdir()) == []
        events = await service.repository.events(
            first.proposal_id,
            workspace_id="knowledge-local",
            owner_id="local",
            thread_id="thread-1",
        )
        assert [event.event_type for event in events] == ["proposal_created"]
        with pytest.raises(KnowledgeSourceProposalNotFoundError):
            await service.repository.get(
                first.proposal_id,
                workspace_id="knowledge-local",
                owner_id="other",
                thread_id="thread-1",
            )
    finally:
        await redis.aclose()
        await engine.dispose()


async def test_approve_materializes_snapshot_queues_job_and_leaves_wiki_reviewable(
    tmp_path: Path,
) -> None:
    service, job_service, store, engine, redis = await _services(tmp_path)
    artifact_ref = _archive_web_evidence(tmp_path / ".coding")
    try:
        pending = await service.propose(
            owner_id="local",
            workspace_id="knowledge-local",
            thread_id="thread-1",
            run_id="run-1",
            artifact_ref=artifact_ref,
            reason="Keep this source",
            evidence_refs=(),
        )
        with pytest.raises(KnowledgeSourceProposalConflictError):
            await service.approve(
                pending.proposal_id,
                owner_id="local",
                workspace_id="knowledge-local",
                thread_id="thread-1",
                expected_revision=99,
                decided_by="local",
            )

        approved = await service.approve(
            pending.proposal_id,
            owner_id="local",
            workspace_id="knowledge-local",
            thread_id="thread-1",
            expected_revision=pending.revision,
            decided_by="local",
        )

        assert approved.status == "approved"
        assert approved.job_id is not None
        assert "web-evidence" in store.source_roots
        store.source_roots.pop("web-evidence")
        job_service._source_ids.pop("web-evidence")  # restart simulation
        await service.prepare()
        assert "web-evidence" in store.source_roots
        assert "web-evidence" in job_service._source_ids
        snapshot = service.snapshot_root / approved.target_relative_path
        assert snapshot.is_file()
        assert "https://example.com/sage" in snapshot.read_text(encoding="utf-8")
        assert "Sage verified public evidence" in snapshot.read_text(encoding="utf-8")
        for _ in range(10):
            job = await job_service.repository.get_job(approved.job_id)
            if job.status in {"completed", "completed_with_errors"}:
                break
            await job_service.run_once(block_ms=1)
        job = await job_service.repository.get_job(approved.job_id)
        assert job.status == "completed"
        wiki_proposals = store.list_proposals("pending")
        assert len(wiki_proposals) == 1
        assert wiki_proposals[0].source_kind == "web"
        decision = store.get_policy_decision(wiki_proposals[0].proposal_id)
        assert decision is not None
        assert decision.action == "draft"
    finally:
        await redis.aclose()
        await engine.dispose()


async def test_reject_and_tampered_artifact_fail_closed(tmp_path: Path) -> None:
    service, _, _, engine, redis = await _services(tmp_path)
    artifact_ref = _archive_web_evidence(tmp_path / ".coding")
    try:
        pending = await service.propose(
            owner_id="local",
            workspace_id="knowledge-local",
            thread_id="thread-1",
            run_id="run-1",
            artifact_ref=artifact_ref,
            reason="Review this source",
            evidence_refs=(),
        )
        ToolResultStore(tmp_path / ".coding", "thread-1", "run-1").archive(
            "call-fetch",
            "tampered",
            metadata={
                "artifact_kind": "web_fetch",
                "canonical_url": "https://example.com/sage",
                "title": "Sage Evidence",
                "retrieved_at": "2026-07-18T09:30:00Z",
                "content_hash": hashlib.sha256(b"tampered").hexdigest(),
                "media_type": "text/html",
            },
        )
        with pytest.raises(ValueError, match="content hash changed"):
            await service.approve(
                pending.proposal_id,
                owner_id="local",
                workspace_id="knowledge-local",
                thread_id="thread-1",
                expected_revision=pending.revision,
                decided_by="local",
            )
        refreshed = await service.repository.get(
            pending.proposal_id,
            workspace_id="knowledge-local",
            owner_id="local",
            thread_id="thread-1",
        )
        assert refreshed.status == "pending"
        assert refreshed.revision > pending.revision
        rejected = await service.reject(
            pending.proposal_id,
            owner_id="local",
            workspace_id="knowledge-local",
            thread_id="thread-1",
            expected_revision=refreshed.revision,
            decided_by="local",
        )
        assert rejected.status == "rejected"
    finally:
        await redis.aclose()
        await engine.dispose()


async def test_prepare_rejects_symlinked_snapshot_root(tmp_path: Path) -> None:
    service, _, _, engine, redis = await _services(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    service.snapshot_root.rmdir()
    service.snapshot_root.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="private directory tree"):
            await service.prepare()
        assert list(outside.iterdir()) == []
    finally:
        await redis.aclose()
        await engine.dispose()
