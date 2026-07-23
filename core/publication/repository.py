"""PostgreSQL-authoritative lifecycle for public package candidates."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import (
    PublicPublicationCandidateEventRecord,
    PublicPublicationCandidateRecord,
)

from .types import PublicationCandidate, PublicationCandidateEvent


class PublicationCandidateNotFoundError(KeyError):
    """The candidate is absent or outside the authenticated owner scope."""


class PublicationCandidateConflictError(RuntimeError):
    """The candidate revision or immutable public revision conflicts."""


class PublicationCandidateRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        owner_id: str,
        package_id: str,
        package_revision: str,
        package_digest: str,
        package: Mapping[str, object],
        reason: str,
        evidence_refs: Sequence[str],
    ) -> PublicationCandidate:
        scope = (
            PublicPublicationCandidateRecord.owner_id == owner_id,
            PublicPublicationCandidateRecord.package_id == package_id,
            PublicPublicationCandidateRecord.package_revision == package_revision,
        )
        async with self._session_factory() as session:
            existing = await session.scalar(select(PublicPublicationCandidateRecord).where(*scope))
            if existing is not None:
                if existing.package_digest != package_digest:
                    raise PublicationCandidateConflictError(
                        "public package revision already has different content"
                    )
                return _candidate_view(existing)
            record = PublicPublicationCandidateRecord(
                id=f"pubcand_{uuid.uuid4().hex}",
                owner_id=owner_id,
                package_id=package_id,
                package_revision=package_revision,
                package_digest=package_digest,
                package_json=json.dumps(
                    dict(package), ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                reason=reason,
                evidence_refs_json=json.dumps(
                    list(evidence_refs), ensure_ascii=False, separators=(",", ":")
                ),
            )
            try:
                session.add(record)
                await session.flush()
                await self._append_event(session, record, "candidate_created")
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                existing = await session.scalar(
                    select(PublicPublicationCandidateRecord).where(*scope)
                )
                if existing is None:
                    raise
                if existing.package_digest != package_digest:
                    raise PublicationCandidateConflictError(
                        "public package revision already has different content"
                    ) from exc
                return _candidate_view(existing)
            return _candidate_view(record)

    async def list(
        self,
        *,
        owner_id: str,
        status: str | None = None,
        limit: int = 100,
    ) -> tuple[PublicationCandidate, ...]:
        query = (
            select(PublicPublicationCandidateRecord)
            .where(PublicPublicationCandidateRecord.owner_id == owner_id)
            .order_by(PublicPublicationCandidateRecord.created_at.desc())
            .limit(limit)
        )
        if status is not None:
            query = query.where(PublicPublicationCandidateRecord.status == status)
        async with self._session_factory() as session:
            return tuple(_candidate_view(item) for item in (await session.scalars(query)).all())

    async def get(self, candidate_id: str, *, owner_id: str) -> PublicationCandidate:
        async with self._session_factory() as session:
            return _candidate_view(
                await self._scoped_record(session, candidate_id, owner_id=owner_id)
            )

    async def events(
        self, candidate_id: str, *, owner_id: str
    ) -> tuple[PublicationCandidateEvent, ...]:
        async with self._session_factory() as session:
            await self._scoped_record(session, candidate_id, owner_id=owner_id)
            records = (
                await session.scalars(
                    select(PublicPublicationCandidateEventRecord)
                    .where(PublicPublicationCandidateEventRecord.candidate_id == candidate_id)
                    .order_by(PublicPublicationCandidateEventRecord.sequence)
                )
            ).all()
            return tuple(_event_view(item) for item in records)

    async def transition(
        self,
        candidate_id: str,
        *,
        owner_id: str,
        expected_revision: int,
        decided_by: str,
        status: str,
    ) -> PublicationCandidate:
        if status not in {"approved", "rejected"}:
            raise ValueError("unsupported publication candidate transition")
        async with self._session_factory() as session, session.begin():
            record = await self._scoped_record(session, candidate_id, owner_id=owner_id, lock=True)
            if record.revision != expected_revision or record.status != "pending":
                raise PublicationCandidateConflictError("publication candidate revision conflict")
            record.status = status
            record.revision += 1
            record.decided_by = decided_by
            record.decided_at = datetime.now(UTC)
            await self._append_event(session, record, f"candidate_{status}")
            await session.flush()
            return _candidate_view(record)

    @staticmethod
    async def _scoped_record(
        session: AsyncSession,
        candidate_id: str,
        *,
        owner_id: str,
        lock: bool = False,
    ) -> PublicPublicationCandidateRecord:
        query = select(PublicPublicationCandidateRecord).where(
            PublicPublicationCandidateRecord.id == candidate_id,
            PublicPublicationCandidateRecord.owner_id == owner_id,
        )
        if lock:
            query = query.with_for_update()
        record = await session.scalar(query)
        if record is None:
            raise PublicationCandidateNotFoundError(candidate_id)
        return record

    @staticmethod
    async def _append_event(
        session: AsyncSession,
        record: PublicPublicationCandidateRecord,
        event_type: str,
    ) -> None:
        record.event_sequence += 1
        session.add(
            PublicPublicationCandidateEventRecord(
                id=f"pubcevt_{uuid.uuid4().hex}",
                candidate_id=record.id,
                sequence=record.event_sequence,
                event_type=event_type,
                revision=record.revision,
                detail_json="{}",
            )
        )


def _candidate_view(record: PublicPublicationCandidateRecord) -> PublicationCandidate:
    package = json.loads(record.package_json)
    evidence_refs = json.loads(record.evidence_refs_json or "[]")
    return PublicationCandidate(
        candidate_id=record.id,
        owner_id=record.owner_id,
        package_id=record.package_id,
        package_revision=record.package_revision,
        package_digest=record.package_digest,
        package={str(key): value for key, value in package.items()},
        reason=record.reason,
        evidence_refs=tuple(str(item) for item in evidence_refs),
        status=record.status,  # type: ignore[arg-type]
        revision=record.revision,
        decided_by=record.decided_by,
        decided_at=record.decided_at.isoformat() if record.decided_at else None,
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


def _event_view(record: PublicPublicationCandidateEventRecord) -> PublicationCandidateEvent:
    detail = json.loads(record.detail_json or "{}")
    return PublicationCandidateEvent(
        event_id=record.id,
        candidate_id=record.candidate_id,
        sequence=record.sequence,
        event_type=record.event_type,
        revision=record.revision,
        detail={str(key): str(value) for key, value in detail.items()},
        created_at=record.created_at.isoformat(),
    )


__all__ = [
    "PublicationCandidateConflictError",
    "PublicationCandidateNotFoundError",
    "PublicationCandidateRepository",
]
