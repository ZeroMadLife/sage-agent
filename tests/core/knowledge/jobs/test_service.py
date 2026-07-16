"""End-to-end durable worker state-machine coverage."""

from __future__ import annotations

import asyncio
import subprocess
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from core.knowledge import KnowledgeSourceRoot, KnowledgeStore
from core.knowledge.jobs import (
    KnowledgeJobConflictError,
    KnowledgeJobRepository,
    KnowledgeJobService,
    RedisKnowledgeJobQueue,
)
from core.knowledge.parsing import (
    ExternalParseCoordinator,
    ExternalParsePolicy,
    ExternalParseProgress,
    ParsedBlock,
    ParsedDocument,
    ParseProvenance,
    ParseRequest,
)


async def _service(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
    *,
    worker_id: str = "worker-1",
    lease_seconds: float = 0.1,
) -> KnowledgeJobService:
    store, _ = knowledge_store
    repository, queue, _ = job_infrastructure
    service = KnowledgeJobService(
        store,
        repository,
        queue,
        worker_id=worker_id,
        lease_seconds=lease_seconds,
        retry_base_seconds=0,
        poll_seconds=0.01,
    )
    await service.prepare()
    return service


async def _finish(service: KnowledgeJobService, job_id: str, *, limit: int = 20) -> str:
    for _ in range(limit):
        await service.reconcile()
        await service.run_once(block_ms=1)
        job = await service.repository.get_job(job_id)
        if job.status in {"completed", "completed_with_errors", "cancelled"}:
            return job.status
    raise AssertionError("knowledge job did not reach a terminal state")


async def test_batch_import_persists_progress_and_completes_no_change_sync(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    _, vault = knowledge_store
    (vault / "one.md").write_text("# One\n\nDurable.\n", encoding="utf-8")
    (vault / "two.md").write_text("# Two\n\nAuditable.\n", encoding="utf-8")
    (vault / "three.html").write_text(
        "<html><title>Three</title><h1>Three</h1><p>Semantic.</p></html>",
        encoding="utf-8",
    )
    service = await _service(knowledge_store, job_infrastructure)

    first = await service.create_batch("vault")
    assert len(first.job_id) <= 36
    assert all(
        len(item.item_id) <= 36 for item in await service.repository.list_items(first.job_id)
    )
    assert len(await service.repository.list_items(first.job_id, limit=1)) == 1
    assert await _finish(service, first.job_id) == "completed"
    completed = await service.repository.get_job(first.job_id)
    assert completed.pipeline_version == "p2.2-b5-autonomy-policy-v1"
    assert completed.total_items == 3
    assert completed.succeeded_items == 3
    assert completed.latest_sequence >= 6
    assert all(
        len(event.event_id) <= 36 for event in await service.repository.list_events(first.job_id)
    )
    assert any(
        event.status == "parsing" for event in await service.repository.list_events(first.job_id)
    )
    assert any(
        event.status == "understanding"
        for event in await service.repository.list_events(first.job_id)
    )
    proposals = [
        item.proposal_id
        for item in await service.repository.list_items(first.job_id)
        if item.proposal_id is not None
    ]
    artifacts = [service.store.get_parse_artifact(proposal_id) for proposal_id in proposals]
    assert {
        artifact.document.provenance.parser_id
        for artifact in artifacts
        if artifact is not None
    } == {"sage.markdown", "sage.html"}

    repeated = await service.create_batch("vault")
    assert await _finish(service, repeated.job_id) == "completed"
    deduplicated = await service.repository.get_job(repeated.job_id)
    assert deduplicated.total_items == 0
    assert deduplicated.skipped_items == 0
    assert deduplicated.succeeded_items == 0
    repeated_state = await service.repository.get_source_sync_state(
        service._source_ids["vault"]
    )
    assert repeated_state.watermark == 1
    assert repeated_state.scan_status == "idle"


async def test_incremental_sync_detects_rename_and_creates_reviewable_tombstone(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    store, vault = knowledge_store
    changing = vault / "changing.md"
    renamed = vault / "old-name.md"
    changing.write_text("# Changing\n\nFirst revision.\n", encoding="utf-8")
    renamed.write_text("# Rename Me\n\nStable source.\n", encoding="utf-8")
    service = await _service(knowledge_store, job_infrastructure)

    planned, concurrent_replay = await asyncio.gather(
        service.preview_sync("vault"),
        service.preview_sync("vault"),
    )
    assert concurrent_replay.plan_id == planned.plan_id
    initial, replayed_initial = await asyncio.gather(
        service.create_batch("vault"),
        service.create_batch("vault"),
    )
    assert replayed_initial.job_id == initial.job_id
    assert await _finish(service, initial.job_id) == "completed"
    assert initial.sync_plan_id is not None
    assert (await service.repository.get_sync_plan(initial.sync_plan_id)).status == "completed"
    store.rebuild_graph()

    no_change = await service.preview_sync("vault")
    assert no_change.base_watermark == 1
    assert no_change.target_watermark == 1
    assert no_change.changes == ()

    changing.write_text("# Changing\n\nSecond incremental revision.\n", encoding="utf-8")
    renamed.unlink()
    (vault / "new-name.md").write_text(
        "# Rename Me\n\nStable source.\n", encoding="utf-8"
    )
    plan = await service.preview_sync("vault")
    replay = await service.preview_sync("vault")

    assert replay.plan_id == plan.plan_id
    assert [(item.relative_path, item.change_kind) for item in plan.changes] == [
        ("changing.md", "modified"),
        ("new-name.md", "added"),
        ("old-name.md", "deleted"),
    ]

    job = await service.create_batch("vault")
    assert job.sync_plan_id == plan.plan_id
    assert await _finish(service, job.job_id) == "completed"
    items = await service.repository.list_items(job.job_id)
    assert {item.change_kind for item in items} == {"added", "modified", "deleted"}
    deleted = next(item for item in items if item.change_kind == "deleted")
    assert deleted.proposal_id is not None
    tombstone = store.get_proposal(deleted.proposal_id)
    assert tombstone.change_kind == "retraction"
    assert tombstone.status == "pending"

    settled = await service.preview_sync("vault")
    assert settled.base_watermark == 2
    assert settled.changes == ()
    graph_status = store.graph_status()
    assert graph_status is not None
    assert graph_status.stale is True
    assert store.search("incremental")[0].chunk.source_revision.startswith("sha256:")


async def test_deleted_source_is_retracted_only_after_review_and_reprojects_index_graph(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    store, vault = knowledge_store
    deleted = vault / "retire.md"
    deleted.write_text(
        "# Retire Source\n\nUnique retractable evidence phrase.\n",
        encoding="utf-8",
    )
    service = await _service(knowledge_store, job_infrastructure)
    initial = await service.create_batch("vault")
    assert await _finish(service, initial.job_id) == "completed"
    original_page = store.list_pages()[0]
    original_graph = store.rebuild_graph()
    assert store.search("retractable evidence")

    deleted.unlink()
    deletion = await service.create_batch("vault")
    assert await _finish(service, deletion.job_id) == "completed"
    [deleted_item] = await service.repository.list_items(deletion.job_id)
    assert deleted_item.change_kind == "deleted"
    assert deleted_item.proposal_id is not None
    tombstone = store.get_proposal(deleted_item.proposal_id)
    assert tombstone.status == "pending"
    assert store.search("retractable evidence")
    assert store.list_pages()[0].current_revision == original_page.current_revision

    approved = store.approve(tombstone.proposal_id, tombstone.revision)
    assert approved.projection_status == "complete"
    assert store.search("retractable evidence") == ()
    graph_status = store.graph_status()
    assert graph_status is not None
    assert graph_status.stale is True
    rebuilt = store.rebuild_graph()
    assert rebuilt.graph_revision != original_graph.graph_revision
    retracted_page = store.list_pages()[0]
    assert len(retracted_page.revisions) == 2
    assert retracted_page.revisions[-1].change_kind == "retraction"


async def test_item_retries_to_dead_letter_then_can_be_retried_individually(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
    monkeypatch: Any,
) -> None:
    store, vault = knowledge_store
    note = vault / "retry.md"
    note.write_text("# Retry\n\nStable source.\n", encoding="utf-8")
    service = await _service(knowledge_store, job_infrastructure)
    original_load = store.load_artifact

    def fail_transiently(_: str, __: object) -> None:
        raise RuntimeError("temporary parser outage")

    monkeypatch.setattr(store, "load_artifact", fail_transiently)

    job = await service.create_batch("vault")
    assert await _finish(service, job.job_id) == "completed_with_errors"
    assert job.sync_plan_id is not None
    assert (await service.repository.get_sync_plan(job.sync_plan_id)).status == "retryable"
    failed_plan = await service.preview_sync("vault")
    assert failed_plan.base_watermark == 0
    assert len(failed_plan.changes) == 1
    failed_state = await service.repository.get_source_sync_state(service._source_ids["vault"])
    assert failed_state.adapter_checkpoint is None
    assert failed_state.scan_status == "retryable"
    [failed] = await service.repository.list_items(job.job_id)
    assert failed.status == "dead_letter"
    assert failed.attempts == 3
    assert "knowledge ingestion failed" in (failed.error or "")

    monkeypatch.setattr(store, "load_artifact", original_load)
    await service.retry_item(job.job_id, failed.item_id)
    assert await _finish(service, job.job_id) == "completed"
    assert (await service.repository.get_sync_plan(job.sync_plan_id)).status == "completed"
    settled = await service.preview_sync("vault")
    assert settled.base_watermark == 1
    assert settled.changes == ()
    settled_state = await service.repository.get_source_sync_state(
        service._source_ids["vault"]
    )
    assert settled_state.adapter_checkpoint == settled.target_checkpoint
    assert settled_state.scan_status == "planned"
    [retried] = await service.repository.list_items(job.job_id)
    assert retried.status == "completed"
    assert retried.attempts == 1


async def test_sync_manifests_and_watermarks_are_isolated_by_source_root(
    tmp_path: Path,
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    first_root = tmp_path / "first-vault"
    second_root = tmp_path / "second-vault"
    workspace = tmp_path / "isolated-knowledge"
    first_root.mkdir()
    second_root.mkdir()
    workspace.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    (first_root / "note.md").write_text("# First\n", encoding="utf-8")
    (second_root / "note.md").write_text("# Second\n", encoding="utf-8")
    store = KnowledgeStore(
        workspace,
        workspace / ".sage" / "knowledge.sqlite3",
        {
            "first": KnowledgeSourceRoot("first", "obsidian", "First", first_root),
            "second": KnowledgeSourceRoot("second", "obsidian", "Second", second_root),
        },
    )
    store.initialize()
    service = await _service((store, first_root), job_infrastructure)

    first_job = await service.create_batch("first")
    assert await _finish(service, first_job.job_id) == "completed"
    second_plan = await service.preview_sync("second")
    assert second_plan.base_watermark == 0
    assert [(item.relative_path, item.change_kind) for item in second_plan.changes] == [
        ("note.md", "added")
    ]

    (first_root / "note.md").write_text("# First changed\n", encoding="utf-8")
    first_delta = await service.preview_sync("first")
    second_replay = await service.preview_sync("second")
    assert first_delta.base_watermark == 1
    assert first_delta.changes[0].change_kind == "modified"
    assert second_replay.plan_id == second_plan.plan_id
    assert second_replay.base_watermark == 0


async def test_scanned_pdf_is_dead_lettered_once_with_requires_ocr_code(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    _, vault = knowledge_store
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(buffer)
    (vault / "scan.pdf").write_bytes(buffer.getvalue())
    service = await _service(knowledge_store, job_infrastructure)

    job = await service.create_batch("vault")

    assert await _finish(service, job.job_id) == "completed_with_errors"
    [failed] = await service.repository.list_items(job.job_id)
    assert failed.status == "dead_letter"
    assert failed.attempts == 1
    assert "requires OCR" in (failed.error or "")
    events = await service.repository.list_events(job.job_id)
    failure = next(event for event in events if event.status == "dead_letter")
    assert failure.detail["error_code"] == "requires_ocr"
    assert failure.detail["retryable"] is False


async def test_scanned_pdf_uses_authorized_external_parser_and_persists_progress(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    class OcrAdapter:
        adapter_id = "test.ocr"
        adapter_version = "1.0.0"
        media_types = frozenset({"application/pdf"})

        async def parse(
            self,
            request: ParseRequest,
            *,
            progress: Any,
        ) -> ParsedDocument:
            await progress(
                ExternalParseProgress(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    stage="running",
                    completed_units=1,
                    total_units=1,
                )
            )
            return ParsedDocument(
                document_id="pdoc_external_scan",
                source_id=request.source_id,
                relative_path=request.relative_path,
                source_revision=request.source_revision,
                title="Scan",
                language="zh",
                rendered_markdown="# Scan\n\nRecovered text.\n",
                blocks=(
                    ParsedBlock(
                        block_id="pblk_external_scan",
                        ordinal=0,
                        kind="paragraph",
                        text="Recovered text.",
                        heading_path=("Scan",),
                        page=1,
                        confidence=0.9,
                    ),
                ),
                provenance=ParseProvenance(
                    parser_id=self.adapter_id,
                    parser_version=self.adapter_version,
                    input_revision=request.source_revision,
                    media_type=request.media_type,
                ),
            )

    _, vault = knowledge_store
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(buffer)
    (vault / "scan.pdf").write_bytes(buffer.getvalue())
    service = await _service(knowledge_store, job_infrastructure)
    service.external_parser = ExternalParseCoordinator(
        ExternalParsePolicy(enabled=True, allowed_source_ids=frozenset({"vault"})),
        [OcrAdapter()],
    )

    job = await service.create_batch("vault")

    assert await _finish(service, job.job_id) == "completed"
    [completed] = await service.repository.list_items(job.job_id)
    assert completed.proposal_id is not None
    artifact = service.store.get_parse_artifact(completed.proposal_id)
    proposal = service.store.get_proposal(completed.proposal_id)
    decision = service.store.get_policy_decision(completed.proposal_id)
    assert artifact is not None
    assert artifact.document.provenance.parser_id == "test.ocr"
    assert proposal.status == "pending"
    assert decision is not None
    assert decision.risk_level == "medium"
    assert decision.action == "draft"
    parser_events = [
        event for event in await service.repository.list_events(job.job_id)
        if event.kind == "parser"
    ]
    assert [event.status for event in parser_events] == [
        "selected",
        "running",
        "completed",
    ]
    assert parser_events[1].detail["completed_units"] == 1


async def test_cancel_marks_unclaimed_items_without_processing(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    _, vault = knowledge_store
    (vault / "one.md").write_text("# One\n", encoding="utf-8")
    (vault / "two.md").write_text("# Two\n", encoding="utf-8")
    service = await _service(knowledge_store, job_infrastructure)
    job = await service.create_batch("vault")

    cancelled = await service.cancel_job(job.job_id)

    assert cancelled.status == "cancelled"
    items = await service.repository.list_items(job.job_id)
    assert {item.status for item in items} == {"cancelled"}


async def test_failure_after_cancellation_is_cancelled_not_dead_letter(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    _, vault = knowledge_store
    (vault / "race.md").write_text("# Race\n", encoding="utf-8")
    service = await _service(knowledge_store, job_infrastructure)
    job = await service.create_batch("vault")
    [message] = await service.queue.read(service.worker_id, count=1, block_ms=1)
    item = await service.repository.claim_item(
        message.item_id, worker_id=service.worker_id, lease_seconds=1
    )
    assert item is not None
    await service.repository.cancel_job(job.job_id)

    failed = await service.repository.fail_item(
        item.item_id,
        worker_id=service.worker_id,
        error="late failure",
        retry_delay_seconds=0,
    )

    assert failed.status == "cancelled"
    with pytest.raises(KnowledgeJobConflictError, match="cancelled jobs"):
        await service.repository.retry_item(job.job_id, item.item_id)


async def test_expired_database_lease_and_pending_stream_message_recover_after_restart(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    _, vault = knowledge_store
    (vault / "restart.md").write_text("# Restart\n", encoding="utf-8")
    first = await _service(
        knowledge_store,
        job_infrastructure,
        worker_id="old-worker",
        lease_seconds=0.01,
    )
    job = await first.create_batch("vault")
    [message] = await first.queue.read("old-worker", count=1, block_ms=1)
    claimed = await first.repository.claim_item(
        message.item_id, worker_id="old-worker", lease_seconds=0.01
    )
    assert claimed is not None
    restarted = KnowledgeJobService(
        first.store,
        first.repository,
        first.queue,
        worker_id="new-worker",
        lease_seconds=0.01,
        retry_base_seconds=0,
        poll_seconds=0.01,
    )
    await restarted.prepare()
    # Restart happens before the old lease expires. The pending Redis message
    # is harmlessly acknowledged, then PostgreSQL recovery republishes it.
    assert await restarted.run_once(block_ms=1) is True
    await asyncio.sleep(0.02)

    assert await _finish(restarted, job.job_id) == "completed"
    events = await restarted.repository.list_events(job.job_id)
    assert any(event.kind == "recovery" for event in events)


async def test_reconcile_acknowledges_a_pending_message_after_database_completion(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    _, vault = knowledge_store
    (vault / "ack.md").write_text("# Ack\n", encoding="utf-8")
    service = await _service(knowledge_store, job_infrastructure)
    job = await service.create_batch("vault")
    [message] = await service.queue.read(service.worker_id, count=1, block_ms=1)
    item = await service.repository.claim_item(
        message.item_id, worker_id=service.worker_id, lease_seconds=1
    )
    assert item is not None
    await service.repository.start_applying(item.item_id, worker_id=service.worker_id)
    claim = await service.repository.claim_idempotency(item.item_id)
    assert claim.outcome == "acquired"
    proposal = service.store.ingest("vault", item.relative_path)
    await service.repository.complete_item(
        item.item_id,
        worker_id=service.worker_id,
        proposal_id=proposal.proposal_id,
    )

    await service.reconcile()
    assert await service.run_once(block_ms=1) is True

    assert (await service.repository.get_job(job.job_id)).status == "completed"
    assert await service.queue.redis.xlen(service.queue.stream) == 0
