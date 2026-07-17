"""Batch orchestration and single-worker runtime for knowledge ingestion."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import uuid
from collections.abc import Mapping

from core.knowledge import KnowledgeSourceRoot, KnowledgeStore
from core.knowledge.parsing import (
    DocumentParseError,
    DocumentRequiresOcrError,
    ExternalParseCoordinator,
    ExternalParsePolicyError,
    ExternalParseProgress,
    ExternalParsingFailedError,
    ExternalParsingTransientError,
    ParserNotFoundError,
)
from core.knowledge.sources import (
    KnowledgeSourceAdapter,
    KnowledgeSourceAdapterRegistry,
    KnowledgeSourceError,
    SourceDescriptor,
    default_source_adapter_registry,
)

from .queue import RedisKnowledgeJobQueue
from .repository import KnowledgeJobConflictError, KnowledgeJobRepository
from .sync import build_manifest_hash, build_sync_changes
from .types import (
    KnowledgeJob,
    KnowledgeJobItem,
    KnowledgeSyncPlan,
    QueueMessage,
    ScannedKnowledgeFile,
)

PIPELINE_VERSION = "p2.2-b5-autonomy-policy-v1"
logger = logging.getLogger(__name__)


class KnowledgeJobService:
    """Coordinate scans, persisted state, Redis delivery, and worker lifecycle."""

    def __init__(
        self,
        store: KnowledgeStore,
        repository: KnowledgeJobRepository,
        queue: RedisKnowledgeJobQueue,
        *,
        workspace_id: str = "knowledge-local",
        worker_id: str | None = None,
        lease_seconds: float = 30.0,
        retry_base_seconds: float = 1.0,
        poll_seconds: float = 0.25,
        external_parser: ExternalParseCoordinator | None = None,
        source_adapters: KnowledgeSourceAdapterRegistry | None = None,
    ) -> None:
        self.store = store
        self.repository = repository
        self.queue = queue
        self.workspace_id = workspace_id
        self.worker_id = worker_id or f"knowledge-worker-{uuid.uuid4().hex[:12]}"
        self.lease_seconds = lease_seconds
        self.retry_base_seconds = retry_base_seconds
        self.poll_seconds = poll_seconds
        self.external_parser = external_parser
        self.source_adapters = source_adapters or default_source_adapter_registry()
        self._source_ids: dict[str, str] = {}
        self._source_scan_locks: dict[str, asyncio.Lock] = {}
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._recovered_messages: list[QueueMessage] = []
        self._prepared = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        await self.prepare()
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name=self.worker_id)

    async def prepare(self) -> None:
        """Initialize infrastructure and reconcile persisted work once."""
        if self._prepared:
            return
        await self.queue.initialize()
        await self._register_sources(self.store.source_roots)
        await self.repository.recover_expired_leases()
        self._recovered_messages = await self.queue.recover_pending(
            self.worker_id,
            # P2.2-A deliberately runs one worker, so every unacked delivery
            # belongs to the previous process and can be reclaimed immediately.
            min_idle_ms=0,
        )
        await self.enqueue_ready()
        self._prepared = True

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def create_batch(
        self,
        source_root_id: str,
        relative_directory: str = ".",
        *,
        sync_plan_id: str | None = None,
    ) -> KnowledgeJob:
        await self.prepare()
        normalized_directory = relative_directory.strip() or "."
        if sync_plan_id is None:
            plan = await self.preview_sync(source_root_id, normalized_directory)
        else:
            plan = await self.repository.get_sync_plan(sync_plan_id)
            if (
                plan.source_root_id != source_root_id
                or plan.relative_directory != normalized_directory
            ):
                raise KnowledgeJobConflictError(
                    "knowledge sync plan does not match the requested source scope"
                )
        source_id = self._source_ids[source_root_id]
        files = [
            ScannedKnowledgeFile(
                relative_path=item.relative_path,
                source_revision=item.source_revision or item.previous_revision or "",
                idempotency_key=item.idempotency_key,
                change_kind=item.change_kind,
            )
            for item in plan.changes
        ]
        job = await self.repository.create_job(
            workspace_id=self.workspace_id,
            source_id=source_id,
            relative_directory=normalized_directory,
            pipeline_version=PIPELINE_VERSION,
            files=files,
            sync_plan_id=plan.plan_id,
        )
        await self.enqueue_ready()
        return job

    async def preview_sync(
        self, source_root_id: str, relative_directory: str = "."
    ) -> KnowledgeSyncPlan:
        """Persist and return a deterministic diff without executing it."""

        lock = self._source_scan_locks.setdefault(source_root_id, asyncio.Lock())
        async with lock:
            return await self._preview_sync_locked(source_root_id, relative_directory)

    async def _preview_sync_locked(
        self, source_root_id: str, relative_directory: str
    ) -> KnowledgeSyncPlan:
        """Scan and persist one plan while holding the per-source scan lock."""

        await self.prepare()
        if source_root_id not in self._source_ids:
            await self._register_sources(self.store.source_roots)
        source_id = self._source_ids.get(source_root_id)
        if source_id is None:
            raise KeyError(source_root_id)
        source = self.store.source_roots[source_root_id]
        adapter = self.source_adapters.resolve(source)
        state = await self.repository.begin_source_scan(
            source_id,
            adapter_id=adapter.adapter_id,
            adapter_version=adapter.adapter_version,
        )
        try:
            descriptors, target_checkpoint = await self._scan_adapter(
                adapter,
                source,
                relative_directory,
                checkpoint=state.adapter_checkpoint,
                source_id=source_id,
            )
        except Exception as exc:
            await self.repository.record_source_scan_failure(
                source_id,
                error_code=_source_error_code(exc),
                error_message=_safe_source_error(exc),
            )
            raise
        scanned = [self._scanned_file(source_root_id, item) for item in descriptors]
        base_watermark, previous_manifest, _manifest_hash = await self.repository.get_manifest(
            source_id, relative_directory=relative_directory
        )
        changes = build_sync_changes(
            scanned,
            previous_manifest,
            workspace_id=self.workspace_id,
            source_root_id=source_root_id,
            relative_directory=relative_directory,
            pipeline_version=PIPELINE_VERSION,
        )
        return await self.repository.create_sync_plan(
            workspace_id=self.workspace_id,
            source_id=source_id,
            source_root_id=source_root_id,
            relative_directory=relative_directory.strip() or ".",
            pipeline_version=PIPELINE_VERSION,
            adapter_id=adapter.adapter_id,
            adapter_version=adapter.adapter_version,
            base_checkpoint=state.adapter_checkpoint,
            target_checkpoint=target_checkpoint,
            base_watermark=base_watermark,
            manifest_hash=build_manifest_hash(previous_manifest, changes),
            changes=changes,
        )

    async def cancel_job(self, job_id: str) -> KnowledgeJob:
        return await self.repository.cancel_job(job_id)

    async def retry_item(self, job_id: str, item_id: str) -> KnowledgeJobItem:
        item = await self.repository.retry_item(job_id, item_id)
        await self.queue.publish(item.item_id)
        return item

    async def enqueue_ready(self) -> int:
        published = 0
        for item_id in await self.repository.ready_item_ids():
            if await self.queue.publish(item_id):
                published += 1
        return published

    async def reconcile(self) -> int:
        """Recover expired database leases and republish authoritative ready work."""
        if not self._recovered_messages:
            self._recovered_messages.extend(
                await self.queue.recover_pending(
                    self.worker_id,
                    min_idle_ms=0,
                )
            )
        await self.repository.recover_expired_leases()
        return await self.enqueue_ready()

    async def run_once(self, *, block_ms: int = 1) -> bool:
        if self._recovered_messages:
            message = self._recovered_messages.pop(0)
        else:
            messages = await self.queue.read(self.worker_id, count=1, block_ms=block_ms)
            if not messages:
                return False
            message = messages[0]
        await self._process_message(message)
        return True

    async def _run(self) -> None:
        next_reconcile = 0.0
        loop = asyncio.get_running_loop()
        while not self._stopping.is_set():
            try:
                now = loop.time()
                if now >= next_reconcile:
                    await self.reconcile()
                    next_reconcile = now + max(self.poll_seconds, 0.25)
                processed = await self.run_once(block_ms=max(1, int(self.poll_seconds * 1000)))
                if not processed:
                    await asyncio.sleep(self.poll_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Knowledge worker loop failed; retrying")
                await asyncio.sleep(self.poll_seconds)

    async def _process_message(self, message: QueueMessage) -> None:
        item = await self.repository.claim_item(
            message.item_id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        if item is None:
            await self.queue.acknowledge(message)
            return
        heartbeat_stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat(item.item_id, heartbeat_stop))
        try:
            if await self.repository.is_cancel_requested(item.item_id):
                await self.repository.cancel_claimed_item(item.item_id, worker_id=self.worker_id)
                return
            claim = await self.repository.claim_idempotency(item.item_id)
            if claim.outcome == "duplicate":
                await self.repository.complete_item(
                    item.item_id,
                    worker_id=self.worker_id,
                    proposal_id=claim.proposal_id,
                    skipped=True,
                )
                return
            if claim.outcome == "busy":
                raise KnowledgeJobConflictError("idempotent source revision is busy")
            job = await self.repository.get_job(item.job_id)
            if item.change_kind == "deleted":
                adapter, source_root = await self._adapter_for_job(job)
                descriptor = SourceDescriptor(
                    source_key=item.relative_path,
                    source_revision=item.source_revision,
                    media_type="application/octet-stream",
                    size_bytes=0,
                )
                await self.repository.start_applying(item.item_id, worker_id=self.worker_id)
                proposal = await asyncio.to_thread(
                    self.store.propose_source_retraction,
                    job.source_root_id,
                    item.relative_path,
                    item.source_revision,
                )
                await self.repository.complete_item(
                    item.item_id,
                    worker_id=self.worker_id,
                    proposal_id=proposal.proposal_id,
                )
                await self._acknowledge_source(
                    adapter, source_root, descriptor, proposal.proposal_id
                )
                return
            adapter, source_root = await self._adapter_for_job(job)
            descriptor = SourceDescriptor(
                source_key=item.relative_path,
                source_revision=item.source_revision,
                media_type="application/octet-stream",
                size_bytes=0,
            )
            artifact = await adapter.fetch(source_root, descriptor)
            await self.repository.start_parsing(item.item_id, worker_id=self.worker_id)
            source = await asyncio.to_thread(
                self.store.load_artifact, job.source_root_id, artifact
            )
            request = source.parse_request()
            try:
                document = await asyncio.to_thread(self.store.parser_registry.parse, request)
            except DocumentRequiresOcrError:
                if self.external_parser is None:
                    raise

                async def report(progress: ExternalParseProgress) -> None:
                    await self.repository.record_parser_progress(
                        item.item_id,
                        worker_id=self.worker_id,
                        adapter_id=progress.adapter_id,
                        adapter_version=progress.adapter_version,
                        stage=progress.stage,
                        completed_units=progress.completed_units,
                        total_units=progress.total_units,
                        reason_code=progress.reason_code,
                    )

                document = await self.external_parser.parse(
                    job.source_root_id,
                    request,
                    progress=report,
                )
            await self.repository.start_understanding(
                item.item_id, worker_id=self.worker_id
            )
            prepared = await asyncio.to_thread(
                self.store.prepare_parsed_source, source, document
            )
            if prepared.source_revision != item.source_revision:
                raise KnowledgeJobConflictError("source revision changed; create a new batch")
            if await self.repository.is_cancel_requested(item.item_id):
                raise KnowledgeJobConflictError("job cancellation requested")
            await self.repository.start_applying(item.item_id, worker_id=self.worker_id)
            proposal = await asyncio.to_thread(self.store.ingest_prepared, prepared)
            proposal = await asyncio.to_thread(
                self.store.evaluate_and_apply_policy, proposal.proposal_id
            )
            await self.repository.complete_item(
                item.item_id,
                worker_id=self.worker_id,
                proposal_id=proposal.proposal_id,
            )
            await self._acknowledge_source(
                adapter, source_root, descriptor, proposal.proposal_id
            )
        except Exception as exc:
            delay = self.retry_base_seconds * (2 ** max(item.attempts - 1, 0))
            await self.repository.fail_item(
                item.item_id,
                worker_id=self.worker_id,
                error=_safe_error(exc),
                retry_delay_seconds=delay,
                error_code=_error_code(exc),
                retryable=_is_retryable(exc),
            )
        finally:
            heartbeat_stop.set()
            await heartbeat
            await self.queue.acknowledge(message)

    async def _heartbeat(self, item_id: str, stop: asyncio.Event) -> None:
        interval = max(self.lease_seconds / 3, 0.05)
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
            except TimeoutError:
                pass
            if not await self.repository.heartbeat(
                item_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            ):
                return

    async def _register_sources(self, sources: Mapping[str, KnowledgeSourceRoot]) -> None:
        workspace_name = self.store.workspace_root.name or "knowledge"
        for root_id, source in sources.items():
            adapter = self.source_adapters.resolve(source)
            self._source_ids[root_id] = await self.repository.ensure_workspace(
                self.workspace_id,
                workspace_name,
                root_id=root_id,
                source_kind=source.kind,
                source_label=source.label,
                adapter_id=adapter.adapter_id,
                adapter_version=adapter.adapter_version,
            )

    async def _scan_adapter(
        self,
        adapter: KnowledgeSourceAdapter,
        source: KnowledgeSourceRoot,
        scope: str,
        *,
        checkpoint: str | None,
        source_id: str | None = None,
    ) -> tuple[list[SourceDescriptor], str]:
        descriptors: list[SourceDescriptor] = []
        cursor: str | None = None
        target_checkpoint: str | None = None
        while True:
            page = await adapter.scan(source, scope, checkpoint, cursor, 500)
            if target_checkpoint is None:
                target_checkpoint = page.target_checkpoint
            elif page.target_checkpoint != target_checkpoint:
                raise KnowledgeJobConflictError("knowledge source changed during scan")
            descriptors.extend(page.items)
            cursor = page.next_cursor
            if source_id is not None:
                await self.repository.record_source_scan_page(
                    source_id,
                    adapter_id=adapter.adapter_id,
                    adapter_version=adapter.adapter_version,
                    resume_cursor=cursor,
                )
            if page.complete:
                if cursor is not None:
                    raise KnowledgeJobConflictError("completed source scan returned a cursor")
                break
            if cursor is None:
                raise KnowledgeJobConflictError("incomplete source scan has no cursor")
        if target_checkpoint is None:
            raise KnowledgeJobConflictError("knowledge source scan returned no checkpoint")
        return descriptors, target_checkpoint

    async def _adapter_for_job(
        self,
        job: KnowledgeJob,
    ) -> tuple[KnowledgeSourceAdapter, KnowledgeSourceRoot]:
        source = self.store.source_roots[job.source_root_id]
        adapter_id: str | None = None
        adapter_version: str | None = None
        if job.sync_plan_id:
            plan = await self.repository.get_sync_plan(job.sync_plan_id)
            adapter_id = plan.adapter_id
            adapter_version = plan.adapter_version
        adapter = self.source_adapters.resolve(source, adapter_id=adapter_id)
        if adapter_version is not None and adapter.adapter_version != adapter_version:
            raise KnowledgeJobConflictError("knowledge source adapter version changed")
        return adapter, source

    def _scanned_file(
        self,
        source_root_id: str,
        descriptor: SourceDescriptor,
    ) -> ScannedKnowledgeFile:
        material = (
            f"{self.workspace_id}\0{source_root_id}\0{descriptor.source_key}\0"
            f"{descriptor.source_revision}\0{PIPELINE_VERSION}"
        )
        return ScannedKnowledgeFile(
            relative_path=descriptor.source_key,
            source_revision=descriptor.source_revision,
            idempotency_key=hashlib.sha256(material.encode()).hexdigest(),
        )

    @staticmethod
    async def _acknowledge_source(
        adapter: KnowledgeSourceAdapter,
        source: KnowledgeSourceRoot,
        descriptor: SourceDescriptor,
        outcome: str,
    ) -> None:
        try:
            await adapter.acknowledge(source, descriptor, outcome)
        except Exception:
            logger.warning(
                "Knowledge source acknowledgement failed after canonical commit",
                extra={"adapter_id": adapter.adapter_id},
                exc_info=True,
            )


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, ValueError | KnowledgeJobConflictError):
        return str(exc)[:1000]
    if isinstance(exc, FileNotFoundError):
        return "knowledge source file not found"
    if isinstance(exc, UnicodeError):
        return "knowledge source is not valid UTF-8"
    return f"{type(exc).__name__}: knowledge ingestion failed"


def _safe_source_error(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "knowledge source is not available"
    if isinstance(exc, KnowledgeSourceError | KnowledgeJobConflictError):
        return str(exc)[:1000]
    return "knowledge source scan failed"


def _source_error_code(exc: Exception) -> str:
    if isinstance(exc, FileNotFoundError):
        return "source_unavailable"
    if isinstance(exc, KnowledgeJobConflictError):
        return "source_conflict"
    if isinstance(exc, KnowledgeSourceError):
        return "source_invalid"
    return "source_scan_failed"


def _error_code(exc: Exception) -> str:
    if isinstance(exc, DocumentRequiresOcrError):
        return "requires_ocr"
    if isinstance(exc, ExternalParsePolicyError):
        return "external_parse_forbidden"
    if isinstance(exc, ExternalParsingFailedError):
        return "external_parse_failed"
    if isinstance(exc, ExternalParsingTransientError):
        return "external_parse_unavailable"
    if isinstance(exc, ParserNotFoundError):
        return "unsupported_format"
    if isinstance(exc, DocumentParseError):
        return "parse_failed"
    if isinstance(exc, KnowledgeJobConflictError):
        return "source_conflict"
    if isinstance(exc, FileNotFoundError):
        return "source_missing"
    if isinstance(exc, ValueError | UnicodeError):
        return "invalid_source"
    return "transient_error"


def _is_retryable(exc: Exception) -> bool:
    return not isinstance(
        exc,
        ValueError
        | ParserNotFoundError
        | KnowledgeJobConflictError
        | FileNotFoundError
        | ExternalParsingFailedError
        | ExternalParsePolicyError,
    )
