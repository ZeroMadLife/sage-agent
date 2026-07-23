"""PostgreSQL-authoritative source proposal lifecycle."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import (
    KnowledgeSourceProposalEventRecord,
    KnowledgeSourceProposalRecord,
)

from .types import KnowledgeSourceProposal, KnowledgeSourceProposalEvent


class KnowledgeSourceProposalNotFoundError(KeyError):
    """The proposal is absent or outside the requested owner/thread scope."""


class KnowledgeSourceProposalConflictError(RuntimeError):
    """A proposal transition failed its optimistic revision guard."""


class KnowledgeSourceProposalRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        workspace_id: str,
        owner_id: str,
        thread_id: str,
        run_id: str,
        artifact_ref: str,
        canonical_url: str,
        title: str,
        media_type: str,
        retrieved_at: str,
        content_hash: str,
        reason: str,
        evidence_refs: Sequence[str],
    ) -> KnowledgeSourceProposal:
        identity = (
            KnowledgeSourceProposalRecord.workspace_id == workspace_id,
            KnowledgeSourceProposalRecord.owner_id == owner_id,
            KnowledgeSourceProposalRecord.thread_id == thread_id,
            KnowledgeSourceProposalRecord.run_id == run_id,
            KnowledgeSourceProposalRecord.artifact_ref == artifact_ref,
            KnowledgeSourceProposalRecord.content_hash == content_hash,
        )
        async with self._session_factory() as session:
            existing = await session.scalar(select(KnowledgeSourceProposalRecord).where(*identity))
            if existing is not None:
                return _proposal_view(existing)
            proposal = KnowledgeSourceProposalRecord(
                id=f"ksprop_{uuid.uuid4().hex}",
                workspace_id=workspace_id,
                owner_id=owner_id,
                thread_id=thread_id,
                run_id=run_id,
                artifact_ref=artifact_ref,
                canonical_url=canonical_url,
                title=title,
                media_type=media_type,
                retrieved_at=retrieved_at,
                content_hash=content_hash,
                reason=reason,
                evidence_refs_json=json.dumps(
                    list(evidence_refs), ensure_ascii=False, separators=(",", ":")
                ),
            )
            try:
                session.add(proposal)
                await session.flush()
                await self._append_event(session, proposal, "proposal_created")
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(
                    select(KnowledgeSourceProposalRecord).where(*identity)
                )
                if existing is None:
                    raise
                return _proposal_view(existing)
            return _proposal_view(proposal)

    async def list(
        self,
        *,
        workspace_id: str,
        owner_id: str,
        thread_id: str,
        status: str | None = None,
        limit: int = 100,
    ) -> tuple[KnowledgeSourceProposal, ...]:
        query = (
            select(KnowledgeSourceProposalRecord)
            .where(
                KnowledgeSourceProposalRecord.workspace_id == workspace_id,
                KnowledgeSourceProposalRecord.owner_id == owner_id,
                KnowledgeSourceProposalRecord.thread_id == thread_id,
            )
            .order_by(KnowledgeSourceProposalRecord.created_at.desc())
            .limit(limit)
        )
        if status is not None:
            query = query.where(KnowledgeSourceProposalRecord.status == status)
        async with self._session_factory() as session:
            return tuple(_proposal_view(item) for item in (await session.scalars(query)).all())

    async def has_materialized_source(self, *, workspace_id: str) -> bool:
        """Return whether restart recovery must re-register the private source root."""

        async with self._session_factory() as session:
            proposal_id = await session.scalar(
                select(KnowledgeSourceProposalRecord.id)
                .where(
                    KnowledgeSourceProposalRecord.workspace_id == workspace_id,
                    KnowledgeSourceProposalRecord.job_id.is_not(None),
                )
                .limit(1)
            )
            return proposal_id is not None

    async def get(
        self,
        proposal_id: str,
        *,
        workspace_id: str,
        owner_id: str,
        thread_id: str,
    ) -> KnowledgeSourceProposal:
        async with self._session_factory() as session:
            proposal = await self._scoped_record(
                session,
                proposal_id,
                workspace_id=workspace_id,
                owner_id=owner_id,
                thread_id=thread_id,
            )
            return _proposal_view(proposal)

    async def events(
        self,
        proposal_id: str,
        *,
        workspace_id: str,
        owner_id: str,
        thread_id: str,
    ) -> tuple[KnowledgeSourceProposalEvent, ...]:
        async with self._session_factory() as session:
            await self._scoped_record(
                session,
                proposal_id,
                workspace_id=workspace_id,
                owner_id=owner_id,
                thread_id=thread_id,
            )
            events = (
                await session.scalars(
                    select(KnowledgeSourceProposalEventRecord)
                    .where(KnowledgeSourceProposalEventRecord.proposal_id == proposal_id)
                    .order_by(KnowledgeSourceProposalEventRecord.sequence)
                )
            ).all()
            return tuple(_event_view(item) for item in events)

    async def claim_applying(
        self,
        proposal_id: str,
        *,
        workspace_id: str,
        owner_id: str,
        thread_id: str,
        expected_revision: int,
        decided_by: str,
    ) -> KnowledgeSourceProposal:
        async with self._session_factory() as session, session.begin():
            proposal = await self._scoped_record(
                session,
                proposal_id,
                workspace_id=workspace_id,
                owner_id=owner_id,
                thread_id=thread_id,
                lock=True,
            )
            if proposal.revision != expected_revision:
                raise KnowledgeSourceProposalConflictError("source proposal revision conflict")
            if proposal.status == "applying":
                return _proposal_view(proposal)
            if proposal.status != "pending":
                raise KnowledgeSourceProposalConflictError("source proposal is already decided")
            proposal.status = "applying"
            proposal.revision += 1
            proposal.decided_by = decided_by
            proposal.last_error = None
            await self._append_event(session, proposal, "proposal_applying")
            return _proposal_view(proposal)

    async def attach_job(
        self,
        proposal_id: str,
        *,
        job_id: str,
        target_relative_path: str,
    ) -> KnowledgeSourceProposal:
        async with self._session_factory() as session, session.begin():
            proposal = await session.get(
                KnowledgeSourceProposalRecord, proposal_id, with_for_update=True
            )
            if proposal is None:
                raise KnowledgeSourceProposalNotFoundError(proposal_id)
            if proposal.status != "applying":
                raise KnowledgeSourceProposalConflictError("source proposal is not applying")
            if proposal.job_id not in {None, job_id}:
                raise KnowledgeSourceProposalConflictError("source proposal job changed")
            if proposal.job_id == job_id and proposal.target_relative_path == target_relative_path:
                return _proposal_view(proposal)
            proposal.job_id = job_id
            proposal.target_relative_path = target_relative_path
            proposal.revision += 1
            await self._append_event(
                session,
                proposal,
                "knowledge_job_created",
                detail={"job_id": job_id},
            )
            return _proposal_view(proposal)

    async def mark_approved(self, proposal_id: str) -> KnowledgeSourceProposal:
        async with self._session_factory() as session, session.begin():
            proposal = await session.get(
                KnowledgeSourceProposalRecord, proposal_id, with_for_update=True
            )
            if proposal is None:
                raise KnowledgeSourceProposalNotFoundError(proposal_id)
            if proposal.status == "approved":
                return _proposal_view(proposal)
            if proposal.status != "applying" or proposal.job_id is None:
                raise KnowledgeSourceProposalConflictError("source proposal has no durable job")
            proposal.status = "approved"
            proposal.revision += 1
            proposal.decided_at = datetime.now(UTC)
            await self._append_event(session, proposal, "proposal_approved")
            return _proposal_view(proposal)

    async def mark_failed(self, proposal_id: str, error: str) -> KnowledgeSourceProposal:
        async with self._session_factory() as session, session.begin():
            proposal = await session.get(
                KnowledgeSourceProposalRecord, proposal_id, with_for_update=True
            )
            if proposal is None:
                raise KnowledgeSourceProposalNotFoundError(proposal_id)
            if proposal.status != "applying":
                return _proposal_view(proposal)
            proposal.status = "pending"
            proposal.revision += 1
            proposal.last_error = error[:1000]
            await self._append_event(
                session,
                proposal,
                "proposal_apply_failed",
                detail={"error": error[:200]},
            )
            return _proposal_view(proposal)

    async def reject(
        self,
        proposal_id: str,
        *,
        workspace_id: str,
        owner_id: str,
        thread_id: str,
        expected_revision: int,
        decided_by: str,
    ) -> KnowledgeSourceProposal:
        async with self._session_factory() as session, session.begin():
            proposal = await self._scoped_record(
                session,
                proposal_id,
                workspace_id=workspace_id,
                owner_id=owner_id,
                thread_id=thread_id,
                lock=True,
            )
            if proposal.revision != expected_revision or proposal.status != "pending":
                raise KnowledgeSourceProposalConflictError("source proposal revision conflict")
            proposal.status = "rejected"
            proposal.revision += 1
            proposal.decided_by = decided_by
            proposal.decided_at = datetime.now(UTC)
            await self._append_event(session, proposal, "proposal_rejected")
            return _proposal_view(proposal)

    @staticmethod
    async def _scoped_record(
        session: AsyncSession,
        proposal_id: str,
        *,
        workspace_id: str,
        owner_id: str,
        thread_id: str,
        lock: bool = False,
    ) -> KnowledgeSourceProposalRecord:
        query = select(KnowledgeSourceProposalRecord).where(
            KnowledgeSourceProposalRecord.id == proposal_id,
            KnowledgeSourceProposalRecord.workspace_id == workspace_id,
            KnowledgeSourceProposalRecord.owner_id == owner_id,
            KnowledgeSourceProposalRecord.thread_id == thread_id,
        )
        if lock:
            query = query.with_for_update()
        proposal = await session.scalar(query)
        if proposal is None:
            raise KnowledgeSourceProposalNotFoundError(proposal_id)
        return proposal

    @staticmethod
    async def _append_event(
        session: AsyncSession,
        proposal: KnowledgeSourceProposalRecord,
        event_type: str,
        *,
        detail: dict[str, str] | None = None,
    ) -> None:
        proposal.event_sequence += 1
        session.add(
            KnowledgeSourceProposalEventRecord(
                id=f"kspevt_{uuid.uuid4().hex}",
                proposal_id=proposal.id,
                sequence=proposal.event_sequence,
                event_type=event_type,
                revision=proposal.revision,
                detail_json=json.dumps(detail or {}, ensure_ascii=False, sort_keys=True),
            )
        )


def _proposal_view(value: KnowledgeSourceProposalRecord) -> KnowledgeSourceProposal:
    evidence = json.loads(value.evidence_refs_json or "[]")
    return KnowledgeSourceProposal(
        proposal_id=value.id,
        workspace_id=value.workspace_id,
        owner_id=value.owner_id,
        thread_id=value.thread_id,
        run_id=value.run_id,
        artifact_ref=value.artifact_ref,
        source_kind=value.source_kind,
        canonical_url=value.canonical_url,
        title=value.title,
        media_type=value.media_type,
        retrieved_at=value.retrieved_at,
        content_hash=value.content_hash,
        reason=value.reason,
        evidence_refs=tuple(str(item) for item in evidence),
        status=value.status,  # type: ignore[arg-type]
        revision=value.revision,
        target_root_id=value.target_root_id,
        target_relative_path=value.target_relative_path,
        job_id=value.job_id,
        last_error=value.last_error,
        decided_by=value.decided_by,
        decided_at=value.decided_at.isoformat() if value.decided_at else None,
        created_at=value.created_at.isoformat(),
        updated_at=value.updated_at.isoformat(),
    )


def _event_view(value: KnowledgeSourceProposalEventRecord) -> KnowledgeSourceProposalEvent:
    detail = json.loads(value.detail_json or "{}")
    return KnowledgeSourceProposalEvent(
        event_id=value.id,
        proposal_id=value.proposal_id,
        sequence=value.sequence,
        event_type=value.event_type,
        revision=value.revision,
        detail={str(key): str(item) for key, item in detail.items()},
        created_at=value.created_at.isoformat(),
    )


__all__ = [
    "KnowledgeSourceProposalConflictError",
    "KnowledgeSourceProposalNotFoundError",
    "KnowledgeSourceProposalRepository",
]
