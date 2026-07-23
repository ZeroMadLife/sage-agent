"""Persistence and optimistic-transition tests for publication candidates."""

from __future__ import annotations

import hashlib

import pytest

from core.publication import (
    PublicationCandidateConflictError,
    PublicationCandidateRepository,
    PublicationCandidateService,
)
from db.database import create_engine, create_session_factory
from db.migrations import init_db


def _package(revision: str = "2026-07-23.1", content: str = "公开学习内容") -> dict:
    return {
        "package_id": "sage-public",
        "revision": revision,
        "documents": [
            {
                "id": "learning-note",
                "title": "学习笔记",
                "url": "https://example.com/learning-note",
                "revision": revision,
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
        ],
    }


@pytest.fixture
async def service(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'publication.sqlite3'}")
    factory = create_session_factory(engine)
    await init_db(engine)
    try:
        yield PublicationCandidateService(PublicationCandidateRepository(factory))
    finally:
        await engine.dispose()


async def test_candidate_is_idempotent_and_keeps_append_only_decision_events(service) -> None:
    first = await service.create(
        owner_id="owner-a",
        package=_package(),
        reason="准备发布学习记录",
        evidence_refs=("knowledge:page:learning-note@r1",),
    )
    repeated = await service.create(
        owner_id="owner-a",
        package=_package(),
        reason="重复提交不覆盖原始审核理由",
        evidence_refs=("other",),
    )

    approved = await service.repository.transition(
        first.candidate_id,
        owner_id="owner-a",
        expected_revision=1,
        decided_by="owner-a",
        status="approved",
    )
    events = await service.repository.events(first.candidate_id, owner_id="owner-a")

    assert repeated.candidate_id == first.candidate_id
    assert repeated.reason == "准备发布学习记录"
    assert approved.revision == 2
    assert approved.status == "approved"
    assert [event.event_type for event in events] == ["candidate_created", "candidate_approved"]
    assert [event.revision for event in events] == [1, 2]


async def test_same_public_revision_cannot_be_reused_with_different_content(service) -> None:
    await service.create(owner_id="owner-a", package=_package(), reason="", evidence_refs=())

    with pytest.raises(PublicationCandidateConflictError, match="different content"):
        await service.create(
            owner_id="owner-a",
            package=_package(content="被替换的内容"),
            reason="",
            evidence_refs=(),
        )


async def test_stage_artifact_requires_approval_and_is_deterministic(service) -> None:
    candidate = await service.create(
        owner_id="owner-a", package=_package(), reason="", evidence_refs=()
    )
    with pytest.raises(PublicationCandidateConflictError, match="approved"):
        await service.stage_artifact(candidate.candidate_id, owner_id="owner-a")

    await service.repository.transition(
        candidate.candidate_id,
        owner_id="owner-a",
        expected_revision=1,
        decided_by="owner-a",
        status="approved",
    )
    first = await service.stage_artifact(candidate.candidate_id, owner_id="owner-a")
    second = await service.stage_artifact(candidate.candidate_id, owner_id="owner-a")

    assert first == second
    assert first["stage_request"] == {"action": "stage", "package": _package()}
    assert first["candidate_revision"] == 2
