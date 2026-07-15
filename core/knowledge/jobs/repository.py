"""PostgreSQL-authoritative repository for durable knowledge ingestion."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import (
    KnowledgeIdempotencyRecord,
    KnowledgeIngestItemRecord,
    KnowledgeIngestJobRecord,
    KnowledgeJobEventRecord,
    KnowledgeSourceRecord,
    KnowledgeWorkspaceRecord,
)

from .types import (
    TERMINAL_ITEM_STATUSES,
    IdempotencyClaim,
    KnowledgeJob,
    KnowledgeJobEvent,
    KnowledgeJobItem,
    ScannedKnowledgeFile,
)


class KnowledgeJobNotFoundError(KeyError):
    """The requested job or item is outside the configured workspace."""


class KnowledgeJobConflictError(RuntimeError):
    """The requested transition is invalid for the current state."""


class KnowledgeJobRepository:
    """Persist job state before a queue message or browser event is emitted."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def ensure_workspace(
        self,
        workspace_id: str,
        workspace_name: str,
        *,
        root_id: str,
        source_kind: str,
        source_label: str,
    ) -> str:
        source_id = _stable_id("ksrc", f"{workspace_id}\0{root_id}")
        async with self._session_factory() as session, session.begin():
            workspace = await session.get(KnowledgeWorkspaceRecord, workspace_id)
            if workspace is None:
                session.add(KnowledgeWorkspaceRecord(id=workspace_id, name=workspace_name))
            else:
                workspace.name = workspace_name
            source = await session.get(KnowledgeSourceRecord, source_id)
            if source is None:
                session.add(
                    KnowledgeSourceRecord(
                        id=source_id,
                        workspace_id=workspace_id,
                        root_id=root_id,
                        kind=source_kind,
                        label=source_label,
                    )
                )
            else:
                source.kind = source_kind
                source.label = source_label
        return source_id

    async def create_job(
        self,
        *,
        workspace_id: str,
        source_id: str,
        relative_directory: str,
        pipeline_version: str,
        files: Sequence[ScannedKnowledgeFile],
        max_attempts: int = 3,
    ) -> KnowledgeJob:
        job_id = _random_id("kjob")
        async with self._session_factory() as session, session.begin():
            job = KnowledgeIngestJobRecord(
                id=job_id,
                workspace_id=workspace_id,
                source_id=source_id,
                relative_directory=relative_directory,
                pipeline_version=pipeline_version,
                total_items=len(files),
            )
            session.add(job)
            await session.flush()
            for scanned in files:
                session.add(
                    KnowledgeIngestItemRecord(
                        id=_random_id("kitem"),
                        job_id=job_id,
                        source_id=source_id,
                        relative_path=scanned.relative_path,
                        source_revision=scanned.source_revision,
                        idempotency_key=scanned.idempotency_key,
                        max_attempts=max_attempts,
                    )
                )
            await self._append_event(
                session,
                job,
                kind="job",
                status="queued",
                detail={"total_items": len(files)},
            )
            if not files:
                job.status = "completed"
                job.completed_at = _now()
                await self._append_event(session, job, kind="job", status="completed")
            await session.flush()
            result = await self._job_view(session, job)
        return result

    async def get_job(self, job_id: str) -> KnowledgeJob:
        async with self._session_factory() as session:
            job = await session.get(KnowledgeIngestJobRecord, job_id)
            if job is None:
                raise KnowledgeJobNotFoundError(job_id)
            return await self._job_view(session, job)

    async def list_jobs(self, *, limit: int = 30) -> list[KnowledgeJob]:
        async with self._session_factory() as session:
            jobs = (
                await session.scalars(
                    select(KnowledgeIngestJobRecord)
                    .order_by(KnowledgeIngestJobRecord.created_at.desc())
                    .limit(limit)
                )
            ).all()
            return [await self._job_view(session, job) for job in jobs]

    async def list_items(
        self,
        job_id: str,
        *,
        statuses: set[str] | None = None,
        limit: int | None = None,
    ) -> list[KnowledgeJobItem]:
        async with self._session_factory() as session:
            if await session.get(KnowledgeIngestJobRecord, job_id) is None:
                raise KnowledgeJobNotFoundError(job_id)
            statement = select(KnowledgeIngestItemRecord).where(
                KnowledgeIngestItemRecord.job_id == job_id
            )
            if statuses:
                statement = statement.where(KnowledgeIngestItemRecord.status.in_(statuses))
            statement = statement.order_by(KnowledgeIngestItemRecord.relative_path)
            if limit is not None:
                statement = statement.limit(limit)
            items = (await session.scalars(statement)).all()
            return [_item_view(item) for item in items]

    async def list_events(
        self, job_id: str, *, after: int = 0, limit: int = 200
    ) -> list[KnowledgeJobEvent]:
        async with self._session_factory() as session:
            if await session.get(KnowledgeIngestJobRecord, job_id) is None:
                raise KnowledgeJobNotFoundError(job_id)
            records = (
                await session.scalars(
                    select(KnowledgeJobEventRecord)
                    .where(
                        KnowledgeJobEventRecord.job_id == job_id,
                        KnowledgeJobEventRecord.sequence > after,
                    )
                    .order_by(KnowledgeJobEventRecord.sequence)
                    .limit(limit)
                )
            ).all()
            return [_event_view(record) for record in records]

    async def ready_item_ids(self, *, limit: int = 1000) -> list[str]:
        now = _now()
        async with self._session_factory() as session:
            return list(
                (
                    await session.scalars(
                        select(KnowledgeIngestItemRecord.id)
                        .join(
                            KnowledgeIngestJobRecord,
                            KnowledgeIngestJobRecord.id == KnowledgeIngestItemRecord.job_id,
                        )
                        .where(
                            KnowledgeIngestItemRecord.status.in_(("queued", "retry_wait")),
                            KnowledgeIngestJobRecord.cancel_requested.is_(False),
                            (
                                KnowledgeIngestItemRecord.next_attempt_at.is_(None)
                                | (KnowledgeIngestItemRecord.next_attempt_at <= now)
                            ),
                        )
                        .order_by(KnowledgeIngestItemRecord.created_at)
                        .limit(limit)
                    )
                ).all()
            )

    async def claim_item(
        self, item_id: str, *, worker_id: str, lease_seconds: float
    ) -> KnowledgeJobItem | None:
        now = _now()
        async with self._session_factory() as session, session.begin():
            job_id = await session.scalar(
                select(KnowledgeIngestItemRecord.job_id).where(
                    KnowledgeIngestItemRecord.id == item_id
                )
            )
            if job_id is None:
                return None
            job = await session.scalar(
                select(KnowledgeIngestJobRecord)
                .where(KnowledgeIngestJobRecord.id == job_id)
                .with_for_update()
            )
            item = await session.scalar(
                select(KnowledgeIngestItemRecord)
                .where(KnowledgeIngestItemRecord.id == item_id)
                .with_for_update()
            )
            if item is None or job is None:
                return None
            if job.cancel_requested:
                return None
            eligible = item.status in {"queued", "retry_wait"} and (
                item.next_attempt_at is None or _as_utc(item.next_attempt_at) <= now
            )
            if not eligible:
                return None
            item.status = "claimed"
            item.attempts += 1
            item.lease_owner = worker_id
            item.lease_expires_at = now + timedelta(seconds=lease_seconds)
            item.next_attempt_at = None
            item.error = None
            if job.started_at is None:
                job.started_at = now
            job.status = "running"
            await self._append_event(
                session,
                job,
                item=item,
                kind="item",
                status="claimed",
                detail={"relative_path": item.relative_path, "attempt": item.attempts},
            )
            return _item_view(item)

    async def heartbeat(self, item_id: str, *, worker_id: str, lease_seconds: float) -> bool:
        async with self._session_factory() as session, session.begin():
            item = await session.scalar(
                select(KnowledgeIngestItemRecord)
                .where(KnowledgeIngestItemRecord.id == item_id)
                .with_for_update()
            )
            if (
                item is None
                or item.lease_owner != worker_id
                or item.status not in {"claimed", "parsing", "applying"}
            ):
                return False
            item.lease_expires_at = _now() + timedelta(seconds=lease_seconds)
            return True

    async def start_parsing(self, item_id: str, *, worker_id: str) -> KnowledgeJobItem:
        async with self._session_factory() as session, session.begin():
            item, job = await self._locked_item_and_job(session, item_id)
            self._require_lease(item, worker_id)
            if job.cancel_requested:
                raise KnowledgeJobConflictError("job cancellation requested")
            item.status = "parsing"
            await self._append_event(
                session,
                job,
                item=item,
                kind="item",
                status="parsing",
                detail={"relative_path": item.relative_path},
            )
            return _item_view(item)

    async def start_applying(self, item_id: str, *, worker_id: str) -> KnowledgeJobItem:
        async with self._session_factory() as session, session.begin():
            item, job = await self._locked_item_and_job(session, item_id)
            self._require_lease(item, worker_id)
            if job.cancel_requested:
                raise KnowledgeJobConflictError("job cancellation requested")
            item.status = "applying"
            await self._append_event(
                session,
                job,
                item=item,
                kind="item",
                status="applying",
                detail={"relative_path": item.relative_path},
            )
            return _item_view(item)

    async def claim_idempotency(self, item_id: str) -> IdempotencyClaim:
        async with self._session_factory() as session, session.begin():
            item = await session.get(KnowledgeIngestItemRecord, item_id)
            if item is None:
                raise KnowledgeJobNotFoundError(item_id)
            existing = await session.scalar(
                select(KnowledgeIdempotencyRecord)
                .where(KnowledgeIdempotencyRecord.idempotency_key == item.idempotency_key)
                .with_for_update()
            )
            if existing is None:
                try:
                    async with session.begin_nested():
                        session.add(
                            KnowledgeIdempotencyRecord(
                                idempotency_key=item.idempotency_key,
                                owner_item_id=item.id,
                                status="processing",
                            )
                        )
                        await session.flush()
                    return IdempotencyClaim("acquired")
                except IntegrityError:
                    existing = await session.scalar(
                        select(KnowledgeIdempotencyRecord)
                        .where(KnowledgeIdempotencyRecord.idempotency_key == item.idempotency_key)
                        .with_for_update()
                    )
            if existing is None:
                return IdempotencyClaim("busy")
            if existing.status == "completed":
                return IdempotencyClaim("duplicate", existing.proposal_id)
            if existing.owner_item_id == item.id:
                return IdempotencyClaim("acquired")
            if existing.status == "failed":
                existing.owner_item_id = item.id
                existing.status = "processing"
                existing.proposal_id = None
                return IdempotencyClaim("acquired")
            return IdempotencyClaim("busy")

    async def complete_item(
        self,
        item_id: str,
        *,
        worker_id: str,
        proposal_id: str | None,
        skipped: bool = False,
    ) -> KnowledgeJobItem:
        async with self._session_factory() as session, session.begin():
            item, job = await self._locked_item_and_job(session, item_id)
            self._require_lease(item, worker_id)
            item.status = "skipped" if skipped else "completed"
            item.proposal_id = proposal_id
            item.lease_owner = None
            item.lease_expires_at = None
            if not skipped:
                key = await session.get(KnowledgeIdempotencyRecord, item.idempotency_key)
                if key is not None and key.owner_item_id == item.id:
                    key.status = "completed"
                    key.proposal_id = proposal_id
            await self._append_event(
                session,
                job,
                item=item,
                kind="item",
                status=item.status,
                detail={"relative_path": item.relative_path, "proposal_id": proposal_id},
            )
            await self._refresh_job(session, job)
            return _item_view(item)

    async def fail_item(
        self,
        item_id: str,
        *,
        worker_id: str,
        error: str,
        retry_delay_seconds: float,
        error_code: str | None = None,
        retryable: bool = True,
    ) -> KnowledgeJobItem:
        async with self._session_factory() as session, session.begin():
            item, job = await self._locked_item_and_job(session, item_id)
            self._require_lease(item, worker_id)
            item.error = error[:4000]
            item.lease_owner = None
            item.lease_expires_at = None
            if job.cancel_requested:
                item.status = "cancelled"
                item.next_attempt_at = None
                key = await session.get(KnowledgeIdempotencyRecord, item.idempotency_key)
                if key is not None and key.owner_item_id == item.id:
                    key.status = "failed"
            elif retryable and item.attempts < item.max_attempts:
                item.status = "retry_wait"
                item.next_attempt_at = _now() + timedelta(seconds=retry_delay_seconds)
            else:
                item.status = "dead_letter"
                item.next_attempt_at = None
                key = await session.get(KnowledgeIdempotencyRecord, item.idempotency_key)
                if key is not None and key.owner_item_id == item.id:
                    key.status = "failed"
            await self._append_event(
                session,
                job,
                item=item,
                kind="item",
                status=item.status,
                detail={
                    "relative_path": item.relative_path,
                    "attempt": item.attempts,
                    "error": item.error,
                    "error_code": error_code,
                    "retryable": retryable,
                },
            )
            await self._refresh_job(session, job)
            return _item_view(item)

    async def cancel_job(self, job_id: str) -> KnowledgeJob:
        async with self._session_factory() as session, session.begin():
            job = await session.scalar(
                select(KnowledgeIngestJobRecord)
                .where(KnowledgeIngestJobRecord.id == job_id)
                .with_for_update()
            )
            if job is None:
                raise KnowledgeJobNotFoundError(job_id)
            if job.status in {"completed", "completed_with_errors", "cancelled"}:
                raise KnowledgeJobConflictError("knowledge job is already terminal")
            job.cancel_requested = True
            job.status = "cancelling"
            items = (
                await session.scalars(
                    select(KnowledgeIngestItemRecord).where(
                        KnowledgeIngestItemRecord.job_id == job_id,
                        KnowledgeIngestItemRecord.status.in_(("queued", "retry_wait")),
                    )
                )
            ).all()
            for item in items:
                item.status = "cancelled"
                item.next_attempt_at = None
                await self._append_event(
                    session,
                    job,
                    item=item,
                    kind="item",
                    status="cancelled",
                    detail={"relative_path": item.relative_path},
                )
            await self._append_event(session, job, kind="job", status="cancelling")
            await self._refresh_job(session, job)
            return await self._job_view(session, job)

    async def is_cancel_requested(self, item_id: str) -> bool:
        async with self._session_factory() as session:
            result = await session.scalar(
                select(KnowledgeIngestJobRecord.cancel_requested)
                .join(
                    KnowledgeIngestItemRecord,
                    KnowledgeIngestItemRecord.job_id == KnowledgeIngestJobRecord.id,
                )
                .where(KnowledgeIngestItemRecord.id == item_id)
            )
            return bool(result)

    async def cancel_claimed_item(self, item_id: str, *, worker_id: str) -> None:
        async with self._session_factory() as session, session.begin():
            item, job = await self._locked_item_and_job(session, item_id)
            self._require_lease(item, worker_id)
            item.status = "cancelled"
            item.lease_owner = None
            item.lease_expires_at = None
            await self._append_event(
                session,
                job,
                item=item,
                kind="item",
                status="cancelled",
                detail={"relative_path": item.relative_path},
            )
            await self._refresh_job(session, job)

    async def retry_item(self, job_id: str, item_id: str) -> KnowledgeJobItem:
        async with self._session_factory() as session, session.begin():
            item, job = await self._locked_item_and_job(session, item_id)
            if job.id != job_id:
                raise KnowledgeJobNotFoundError(item_id)
            if job.cancel_requested:
                raise KnowledgeJobConflictError("cancelled jobs cannot be retried")
            if item.status != "dead_letter":
                raise KnowledgeJobConflictError("only dead-letter items can be retried")
            item.status = "retry_wait"
            item.attempts = 0
            item.next_attempt_at = _now()
            item.error = None
            job.completed_at = None
            job.status = "running"
            await self._append_event(
                session,
                job,
                item=item,
                kind="item",
                status="retry_wait",
                detail={"relative_path": item.relative_path, "manual": True},
            )
            await self._refresh_job(session, job)
            return _item_view(item)

    async def recover_expired_leases(self) -> list[str]:
        now = _now()
        recovered: list[str] = []
        async with self._session_factory() as session, session.begin():
            item_ids = (
                await session.scalars(
                    select(KnowledgeIngestItemRecord.id).where(
                        KnowledgeIngestItemRecord.status.in_(("claimed", "parsing", "applying")),
                        KnowledgeIngestItemRecord.lease_expires_at <= now,
                    )
                )
            ).all()
            for item_id in item_ids:
                item, job = await self._locked_item_and_job(session, item_id)
                if (
                    item.status not in {"claimed", "parsing", "applying"}
                    or item.lease_expires_at is None
                    or _as_utc(item.lease_expires_at) > now
                ):
                    continue
                item.status = "cancelled" if job.cancel_requested else "retry_wait"
                item.lease_owner = None
                item.lease_expires_at = None
                item.next_attempt_at = None if job.cancel_requested else now
                recovered.append(item.id)
                await self._append_event(
                    session,
                    job,
                    item=item,
                    kind="recovery",
                    status=item.status,
                    detail={"relative_path": item.relative_path},
                )
                await self._refresh_job(session, job)
        return recovered

    async def _locked_item_and_job(
        self, session: AsyncSession, item_id: str
    ) -> tuple[KnowledgeIngestItemRecord, KnowledgeIngestJobRecord]:
        job_id = await session.scalar(
            select(KnowledgeIngestItemRecord.job_id).where(KnowledgeIngestItemRecord.id == item_id)
        )
        if job_id is None:
            raise KnowledgeJobNotFoundError(item_id)
        job = await session.scalar(
            select(KnowledgeIngestJobRecord)
            .where(KnowledgeIngestJobRecord.id == job_id)
            .with_for_update()
        )
        if job is None:
            raise KnowledgeJobNotFoundError(job_id)
        item = await session.scalar(
            select(KnowledgeIngestItemRecord)
            .where(KnowledgeIngestItemRecord.id == item_id)
            .with_for_update()
        )
        if item is None:
            raise KnowledgeJobNotFoundError(item_id)
        return item, job

    @staticmethod
    def _require_lease(item: KnowledgeIngestItemRecord, worker_id: str) -> None:
        if item.lease_owner != worker_id or item.status not in {
            "claimed",
            "parsing",
            "applying",
        }:
            raise KnowledgeJobConflictError("knowledge item lease is not owned")

    async def _refresh_job(self, session: AsyncSession, job: KnowledgeIngestJobRecord) -> None:
        rows = (
            await session.execute(
                select(
                    KnowledgeIngestItemRecord.status,
                    func.count(KnowledgeIngestItemRecord.id),
                )
                .where(KnowledgeIngestItemRecord.job_id == job.id)
                .group_by(KnowledgeIngestItemRecord.status)
            )
        ).all()
        counts: dict[str, int] = {status: int(count) for status, count in rows}
        job.succeeded_items = int(counts.get("completed", 0))
        job.skipped_items = int(counts.get("skipped", 0))
        job.failed_items = int(counts.get("dead_letter", 0))
        job.cancelled_items = int(counts.get("cancelled", 0))
        job.processed_items = sum(int(counts.get(status, 0)) for status in TERMINAL_ITEM_STATUSES)
        previous = job.status
        if job.processed_items == job.total_items:
            if job.cancel_requested:
                job.status = "cancelled"
            elif job.failed_items:
                job.status = "completed_with_errors"
            else:
                job.status = "completed"
            job.completed_at = _now()
        elif job.cancel_requested:
            job.status = "cancelling"
        else:
            job.status = "running"
        if job.status != previous:
            await self._append_event(
                session,
                job,
                kind="job",
                status=job.status,
                detail={
                    "processed_items": job.processed_items,
                    "total_items": job.total_items,
                },
            )

    async def _append_event(
        self,
        session: AsyncSession,
        job: KnowledgeIngestJobRecord,
        *,
        kind: str,
        status: str,
        item: KnowledgeIngestItemRecord | None = None,
        detail: dict[str, str | int | bool | None] | None = None,
    ) -> None:
        job.event_sequence += 1
        session.add(
            KnowledgeJobEventRecord(
                id=_random_id("kevt"),
                job_id=job.id,
                item_id=item.id if item else None,
                sequence=job.event_sequence,
                kind=kind,
                status=status,
                detail_json=json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
            )
        )

    async def _job_view(self, session: AsyncSession, job: KnowledgeIngestJobRecord) -> KnowledgeJob:
        source = await session.get(KnowledgeSourceRecord, job.source_id)
        if source is None:
            raise KnowledgeJobNotFoundError(job.source_id)
        return KnowledgeJob(
            job_id=job.id,
            workspace_id=job.workspace_id,
            source_root_id=source.root_id,
            source_kind=source.kind,
            source_label=source.label,
            relative_directory=job.relative_directory,
            pipeline_version=job.pipeline_version,
            status=job.status,
            cancel_requested=job.cancel_requested,
            total_items=job.total_items,
            processed_items=job.processed_items,
            succeeded_items=job.succeeded_items,
            skipped_items=job.skipped_items,
            failed_items=job.failed_items,
            cancelled_items=job.cancelled_items,
            latest_sequence=job.event_sequence,
            created_at=_as_utc(job.created_at),
            started_at=_as_utc(job.started_at) if job.started_at else None,
            completed_at=_as_utc(job.completed_at) if job.completed_at else None,
            updated_at=_as_utc(job.updated_at),
        )


def _item_view(item: KnowledgeIngestItemRecord) -> KnowledgeJobItem:
    return KnowledgeJobItem(
        item_id=item.id,
        job_id=item.job_id,
        relative_path=item.relative_path,
        source_revision=item.source_revision,
        status=item.status,
        attempts=item.attempts,
        max_attempts=item.max_attempts,
        proposal_id=item.proposal_id,
        error=item.error,
        next_attempt_at=_as_utc(item.next_attempt_at) if item.next_attempt_at else None,
        updated_at=_as_utc(item.updated_at),
    )


def _event_view(record: KnowledgeJobEventRecord) -> KnowledgeJobEvent:
    return KnowledgeJobEvent(
        event_id=record.id,
        job_id=record.job_id,
        item_id=record.item_id,
        sequence=record.sequence,
        kind=record.kind,
        status=record.status,
        detail=json.loads(record.detail_json),
        created_at=_as_utc(record.created_at),
    )


def _stable_id(prefix: str, value: str) -> str:
    import hashlib

    return f"{prefix}_{hashlib.sha256(value.encode()).hexdigest()[:30]}"


def _random_id(prefix: str) -> str:
    """Fit prefixed 120-bit identifiers into the shared VARCHAR(36) columns."""
    return f"{prefix}_{uuid.uuid4().hex[:30]}"


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
