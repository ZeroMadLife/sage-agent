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
    ExternalAdapterError,
    ExternalParseCompleted,
    ExternalParseCoordinator,
    ExternalParsePending,
    ExternalParsePolicy,
    ExternalParseProgress,
    ExternalParseTicket,
    ParsedBlock,
    ParsedDocument,
    ParseProvenance,
    ParseRequest,
)


def _text_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    payload = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode())
    payload.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(payload)


def _external_document(request: ParseRequest, adapter_id: str) -> ParsedDocument:
    return ParsedDocument(
        document_id="pdoc_resumed_external",
        source_id=request.source_id,
        relative_path=request.relative_path,
        source_revision=request.source_revision,
        title="Resumed scan",
        language="zh",
        rendered_markdown="# Resumed scan\n\nRecovered asynchronously.\n",
        blocks=(
            ParsedBlock(
                block_id="pblk_resumed_external",
                ordinal=0,
                kind="paragraph",
                text="Recovered asynchronously.",
                heading_path=("Resumed scan",),
                page=1,
                confidence=0.9,
            ),
        ),
        provenance=ParseProvenance(
            parser_id=adapter_id,
            parser_version="1.0.0",
            input_revision=request.source_revision,
            media_type=request.media_type,
        ),
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
    assert completed.pipeline_version == "h2.5b2-async-document-parsing-v1"
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
        artifact.document.provenance.parser_id for artifact in artifacts if artifact is not None
    } == {"sage.markdown", "sage.html"}

    repeated = await service.create_batch("vault")
    assert await _finish(service, repeated.job_id) == "completed"
    deduplicated = await service.repository.get_job(repeated.job_id)
    assert deduplicated.total_items == 0
    assert deduplicated.skipped_items == 0
    assert deduplicated.succeeded_items == 0
    repeated_state = await service.repository.get_source_sync_state(service._source_ids["vault"])
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
    (vault / "new-name.md").write_text("# Rename Me\n\nStable source.\n", encoding="utf-8")
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
    settled_state = await service.repository.get_source_sync_state(service._source_ids["vault"])
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
        event
        for event in await service.repository.list_events(job.job_id)
        if event.kind == "parser"
    ]
    assert [event.status for event in parser_events] == [
        "selected",
        "running",
        "completed",
    ]
    assert parser_events[1].detail["completed_units"] == 1


async def test_external_pdf_wait_releases_worker_and_resumes_after_restart(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    class ResumableAdapter:
        adapter_id = "test.resumable"
        adapter_version = "1.0.0"
        media_types = frozenset({"application/pdf"})

        def __init__(self) -> None:
            self.submit_calls = 0
            self.resume_calls = 0

        async def submit(self, request: ParseRequest, *, progress: Any) -> ExternalParsePending:
            self.submit_calls += 1
            await progress(
                ExternalParseProgress(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    stage="queued",
                )
            )
            return ExternalParsePending(
                ticket=ExternalParseTicket(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    task_id="remote-task-1",
                ),
                stage="queued",
                retry_after_seconds=0.001,
            )

        async def resume(
            self,
            request: ParseRequest,
            ticket: ExternalParseTicket,
            *,
            progress: Any,
        ) -> ExternalParseCompleted:
            self.resume_calls += 1
            assert ticket.task_id == "remote-task-1"
            await progress(
                ExternalParseProgress(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    stage="running",
                    completed_units=1,
                    total_units=1,
                )
            )
            return ExternalParseCompleted(_external_document(request, self.adapter_id))

        async def parse(self, request: ParseRequest, *, progress: Any) -> ParsedDocument:
            raise AssertionError("Knowledge worker must use resumable dispatch")

    _, vault = knowledge_store
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(buffer)
    (vault / "async-scan.pdf").write_bytes(buffer.getvalue())
    (vault / "ready-while-pdf-waits.md").write_text(
        "# Ready\n\nThis item must not wait for MinerU.\n",
        encoding="utf-8",
    )
    adapter = ResumableAdapter()
    coordinator = ExternalParseCoordinator(
        ExternalParsePolicy(enabled=True, allowed_source_ids=frozenset({"vault"})),
        [adapter],
    )
    first = await _service(knowledge_store, job_infrastructure, worker_id="first-worker")
    first.external_parser = coordinator
    job = await first.create_batch("vault")

    assert await first.run_once(block_ms=1) is True
    items = {item.relative_path: item for item in await first.repository.list_items(job.job_id)}
    waiting = items["async-scan.pdf"]
    assert waiting.status == "external_wait"
    assert waiting.attempts == 1
    assert items["ready-while-pdf-waits.md"].status == "queued"
    ticket = await first.repository.get_external_parse_state(waiting.item_id)
    assert ticket is not None
    assert ticket.task_id == "remote-task-1"
    assert ticket.state == "queued"

    assert await first.run_once(block_ms=1) is True
    items = {item.relative_path: item for item in await first.repository.list_items(job.job_id)}
    assert items["async-scan.pdf"].status == "external_wait"
    assert items["ready-while-pdf-waits.md"].status == "completed"

    await asyncio.sleep(0.002)
    restarted = KnowledgeJobService(
        first.store,
        first.repository,
        first.queue,
        worker_id="restarted-worker",
        lease_seconds=0.1,
        retry_base_seconds=0,
        poll_seconds=0.01,
        external_parser=coordinator,
    )
    await restarted.prepare()

    assert await _finish(restarted, job.job_id) == "completed"
    completed = next(
        item
        for item in await restarted.repository.list_items(job.job_id)
        if item.relative_path == "async-scan.pdf"
    )
    assert completed.attempts == 1
    assert adapter.submit_calls == 1
    assert adapter.resume_calls == 1
    persisted = await restarted.repository.get_external_parse_state(completed.item_id)
    assert persisted is not None
    assert persisted.state == "completed"


async def test_external_pdf_failure_falls_back_to_local_text_parser(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    class FailingAdapter:
        adapter_id = "test.remote"
        adapter_version = "1.0.0"
        media_types = frozenset({"application/pdf"})

        async def submit(
            self,
            request: ParseRequest,
            *,
            progress: Any,
        ) -> ExternalParsePending:
            return ExternalParsePending(
                ticket=ExternalParseTicket(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    task_id="remote-fallback-1",
                ),
                stage="queued",
                retry_after_seconds=0.001,
            )

        async def resume(
            self,
            request: ParseRequest,
            ticket: ExternalParseTicket,
            *,
            progress: Any,
        ) -> ExternalParseCompleted:
            assert ticket.task_id == "remote-fallback-1"
            raise ExternalAdapterError(self.adapter_id, "invalid_result", retryable=False)

        async def parse(self, request: ParseRequest, *, progress: Any) -> ParsedDocument:
            raise AssertionError("Knowledge worker must use resumable dispatch")

    _, vault = knowledge_store
    (vault / "text-layer.pdf").write_bytes(_text_pdf("Local fallback remains available"))
    service = await _service(knowledge_store, job_infrastructure)
    service.external_parser = ExternalParseCoordinator(
        ExternalParsePolicy(enabled=True, allowed_source_ids=frozenset({"vault"})),
        [FailingAdapter()],
    )

    job = await service.create_batch("vault")

    assert await _finish(service, job.job_id) == "completed"
    [completed] = await service.repository.list_items(job.job_id)
    assert completed.proposal_id is not None
    artifact = service.store.get_parse_artifact(completed.proposal_id)
    assert artifact is not None
    assert artifact.document.provenance.parser_id == "sage.pdf.text"
    external = await service.repository.get_external_parse_state(completed.item_id)
    assert external is not None
    assert external.state == "fallback"
    events = await service.repository.list_events(job.job_id)
    fallback = next(
        event for event in events if event.kind == "parser" and event.status == "fallback"
    )
    assert fallback.detail["adapter_id"] == "sage.pdf.text"
    assert fallback.detail["reason_code"] == "external_parse_failed"


async def test_external_pdf_can_replace_failed_ticket_with_next_resumable_adapter(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    class FirstAdapter:
        adapter_id = "test.first"
        adapter_version = "1.0.0"
        media_types = frozenset({"application/pdf"})

        async def submit(self, request: ParseRequest, *, progress: Any) -> ExternalParsePending:
            return ExternalParsePending(
                ticket=ExternalParseTicket(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    task_id="first-task",
                ),
                stage="queued",
                retry_after_seconds=0.001,
            )

        async def resume(
            self,
            request: ParseRequest,
            ticket: ExternalParseTicket,
            *,
            progress: Any,
        ) -> ExternalParseCompleted:
            raise ExternalAdapterError(self.adapter_id, "failed", retryable=False)

        async def parse(self, request: ParseRequest, *, progress: Any) -> ParsedDocument:
            raise AssertionError("Knowledge worker must use resumable dispatch")

    class SecondAdapter:
        adapter_id = "test.second"
        adapter_version = "1.0.0"
        media_types = frozenset({"application/pdf"})

        async def submit(self, request: ParseRequest, *, progress: Any) -> ExternalParsePending:
            return ExternalParsePending(
                ticket=ExternalParseTicket(
                    adapter_id=self.adapter_id,
                    adapter_version=self.adapter_version,
                    task_id="second-task",
                ),
                stage="queued",
                retry_after_seconds=0.001,
            )

        async def resume(
            self,
            request: ParseRequest,
            ticket: ExternalParseTicket,
            *,
            progress: Any,
        ) -> ExternalParseCompleted:
            assert ticket.task_id == "second-task"
            return ExternalParseCompleted(_external_document(request, self.adapter_id))

        async def parse(self, request: ParseRequest, *, progress: Any) -> ParsedDocument:
            raise AssertionError("Knowledge worker must use resumable dispatch")

    _, vault = knowledge_store
    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(buffer)
    (vault / "adapter-fallback.pdf").write_bytes(buffer.getvalue())
    service = await _service(knowledge_store, job_infrastructure)
    service.external_parser = ExternalParseCoordinator(
        ExternalParsePolicy(enabled=True, allowed_source_ids=frozenset({"vault"})),
        [FirstAdapter(), SecondAdapter()],
    )

    job = await service.create_batch("vault")

    assert await _finish(service, job.job_id) == "completed"
    [completed] = await service.repository.list_items(job.job_id)
    assert completed.attempts == 1
    external = await service.repository.get_external_parse_state(completed.item_id)
    assert external is not None
    assert external.adapter_id == "test.second"
    assert external.task_id == "second-task"
    assert external.state == "completed"
    parser_events = [
        event
        for event in await service.repository.list_events(job.job_id)
        if event.kind == "parser"
    ]
    assert any(
        event.status == "failed"
        and event.detail["adapter_id"] == "test.first"
        and event.detail["reason_code"] == "adapter_fallback"
        for event in parser_events
    )


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


async def test_cancel_marks_external_wait_ticket_terminal_and_prevents_requeue(
    knowledge_store: tuple[KnowledgeStore, Path],
    job_infrastructure: tuple[KnowledgeJobRepository, RedisKnowledgeJobQueue, Any],
) -> None:
    _, vault = knowledge_store
    (vault / "waiting.pdf").write_bytes(_text_pdf("Waiting external parse"))
    service = await _service(knowledge_store, job_infrastructure)
    job = await service.create_batch("vault")
    [message] = await service.queue.read(service.worker_id, count=1, block_ms=1)
    item = await service.repository.claim_item(
        message.item_id,
        worker_id=service.worker_id,
        lease_seconds=1,
    )
    assert item is not None
    await service.repository.start_parsing(item.item_id, worker_id=service.worker_id)
    await service.repository.defer_external_parse(
        item.item_id,
        worker_id=service.worker_id,
        adapter_id="mineru.agent",
        adapter_version="1.1.0",
        task_id="remote-cancel-1",
        state="running",
        retry_after_seconds=60,
    )
    await service.queue.acknowledge(message)

    cancelled = await service.cancel_job(job.job_id)

    assert cancelled.status == "cancelled"
    [cancelled_item] = await service.repository.list_items(job.job_id)
    assert cancelled_item.status == "cancelled"
    external = await service.repository.get_external_parse_state(cancelled_item.item_id)
    assert external is not None
    assert external.state == "cancelled"
    assert cancelled_item.item_id not in await service.repository.ready_item_ids()


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
