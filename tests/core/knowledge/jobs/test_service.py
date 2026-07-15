"""End-to-end durable worker state-machine coverage."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from core.knowledge import KnowledgeStore
from core.knowledge.jobs import (
    KnowledgeJobConflictError,
    KnowledgeJobRepository,
    KnowledgeJobService,
    RedisKnowledgeJobQueue,
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


async def test_batch_import_persists_progress_and_skips_duplicate_revision(
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
    assert completed.pipeline_version == "p2.2-b2-multiformat-v1"
    assert completed.total_items == 3
    assert completed.succeeded_items == 3
    assert completed.latest_sequence >= 6
    assert all(
        len(event.event_id) <= 36 for event in await service.repository.list_events(first.job_id)
    )
    assert any(
        event.status == "parsing" for event in await service.repository.list_events(first.job_id)
    )
    proposals = [
        item.proposal_id
        for item in await service.repository.list_items(first.job_id)
        if item.proposal_id is not None
    ]
    assert {
        service.store.get_parse_artifact(proposal_id).document.provenance.parser_id
        for proposal_id in proposals
        if service.store.get_parse_artifact(proposal_id) is not None
    } == {"sage.markdown", "sage.html"}

    repeated = await service.create_batch("vault")
    assert await _finish(service, repeated.job_id) == "completed"
    deduplicated = await service.repository.get_job(repeated.job_id)
    assert deduplicated.skipped_items == 3
    assert deduplicated.succeeded_items == 0


async def test_item_retries_to_dead_letter_then_can_be_retried_individually(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
    monkeypatch: Any,
) -> None:
    store, vault = knowledge_store
    note = vault / "retry.md"
    note.write_text("# Retry\n\nStable source.\n", encoding="utf-8")
    service = await _service(knowledge_store, job_infrastructure)
    original_prepare = store.prepare_ingest

    def fail_transiently(_: str, __: str) -> None:
        raise RuntimeError("temporary parser outage")

    monkeypatch.setattr(store, "prepare_ingest", fail_transiently)

    job = await service.create_batch("vault")
    assert await _finish(service, job.job_id) == "completed_with_errors"
    [failed] = await service.repository.list_items(job.job_id)
    assert failed.status == "dead_letter"
    assert failed.attempts == 3
    assert "knowledge ingestion failed" in (failed.error or "")

    monkeypatch.setattr(store, "prepare_ingest", original_prepare)
    await service.retry_item(job.job_id, failed.item_id)
    assert await _finish(service, job.job_id) == "completed"
    [retried] = await service.repository.list_items(job.job_id)
    assert retried.status == "completed"
    assert retried.attempts == 1


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
