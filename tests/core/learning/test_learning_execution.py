from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sage_harness import (
    EvidenceBundleItem,
    KnowledgeEvidence,
    KnowledgeRetrievalResult,
)

from core.learning.artifact_store import (
    LearningArtifactStore,
    LearningCheckpointConflictError,
    LearningResumeConflictError,
)
from core.learning.execution import LearningExecutionContext, LearningExecutionService
from core.learning.materials import LearningMapService
from core.learning.research import (
    LearningResearchEvidence,
    LearningResearchOutcome,
    LearningResearchReceipt,
)
from core.learning.tasks import (
    LearningClarification,
    LearningLearnerProfile,
    LearningSourcePolicy,
    LearningTask,
)


class FakeKnowledgePort:
    workspace_id = "knowledge-workspace"
    available = True

    async def search(
        self,
        query: str,
        *,
        workspace_id: str,
        token_budget: int,
        top_k: int = 8,
    ) -> KnowledgeRetrievalResult:
        return KnowledgeRetrievalResult(
            query=query,
            workspace_id=workspace_id,
            status="evidence_found",
            token_budget=token_budget,
            used_tokens=80,
            omitted_count=0,
            evidence=(
                KnowledgeEvidence(
                    citation_id="kcite-1",
                    content="Checkpoint stores only bounded recovery state.",
                    page_revision="page-r1",
                    source_revision="source-r1",
                    metadata={"title": "Checkpoint guide"},
                ),
            ),
        )


class BlockingKnowledgePort(FakeKnowledgePort):
    def __init__(self) -> None:
        self.calls = 0
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def search(self, *args: object, **kwargs: object) -> KnowledgeRetrievalResult:
        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
            await self.release_first.wait()
        return await super().search(*args, **kwargs)  # type: ignore[arg-type]


def _task() -> LearningTask:
    return LearningTask(
        version=1,
        workspace_id="workspace-1",
        task_id="ltask-1",
        task_revision=2,
        template_id="general_learning",
        topic="学习 Sage checkpoint",
        desired_outcome="能够解释恢复边界",
        learner_profile=LearningLearnerProfile("beginner", 180),
        source_policy=LearningSourcePolicy(
            web="allowed_when_insufficient",
            domains=("example.com",),
            freshness="current",
        ),
        risk_class="general_education",
        risk_notice=None,
        clarification=LearningClarification((), (), True),
        learning_plan_id=None,
        learning_plan_hash=None,
        dag_hash=None,
        learning_goal_ref={"goal_id": "goal-1", "goal_revision": "goal-r1"},
        status="active",
        created_at="2026-08-25T00:00:00Z",
        updated_at="2026-08-25T00:00:01Z",
    )


class FakeResearchService:
    def __init__(self, outcome: LearningResearchOutcome) -> None:
        self.outcome = outcome
        self.calls = 0

    async def run(self, **kwargs: object) -> LearningResearchOutcome:
        self.calls += 1
        return self.outcome


def _context() -> LearningExecutionContext:
    return LearningExecutionContext(
        thread_id="session-1",
        parent_run_id="run-parent",
        workspace_path="/workspace",
        capability_revision="cap-r1",
        catalog_revision="catalog-r1",
        allowed_capabilities=frozenset({"web:search", "web:fetch"}),
        remaining_token_budget=2_000,
    )


@pytest.mark.asyncio
async def test_knowledge_execution_is_single_stage_and_request_idempotent(tmp_path: Path) -> None:
    service = LearningExecutionService(
        store=LearningArtifactStore(tmp_path / "artifacts.sqlite3"),
        map_service=LearningMapService(knowledge_port=FakeKnowledgePort()),
    )

    first = await service.advance(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        context=_context(),
        expected_checkpoint_revision=0,
        idempotency_key="advance-1",
    )
    assert first.stage == "knowledge_pending"
    assert first.checkpoint_revision == 1
    assert first.artifact_ref.startswith("sage://learning/artifacts/")

    replay = await service.advance(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        context=_context(),
        expected_checkpoint_revision=0,
        idempotency_key="advance-1",
    )
    assert replay == first

    with pytest.raises(LearningCheckpointConflictError):
        await service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            context=_context(),
            expected_checkpoint_revision=1,
            idempotency_key="advance-1",
        )

    with pytest.raises(LearningCheckpointConflictError):
        await service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            context=_context(),
            expected_checkpoint_revision=0,
            idempotency_key="different-request",
        )

    current = first
    for key, expected_stage in (
        ("advance-2", "knowledge_ready"),
        ("advance-3", "synthesize_pending"),
        ("advance-4", "artifact_ready"),
    ):
        current = await service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            context=_context(),
            expected_checkpoint_revision=current.checkpoint_revision,
            idempotency_key=key,
        )
        assert current.stage == expected_stage

    reopened = LearningExecutionService(
        store=LearningArtifactStore(tmp_path / "artifacts.sqlite3"),
        map_service=LearningMapService(knowledge_port=None),
    )
    assert (
        reopened.resume(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            capability_revision="cap-r1",
        )
        == current
    )
    historical_replay = await reopened.advance(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        context=_context(),
        expected_checkpoint_revision=0,
        idempotency_key="advance-1",
    )
    assert historical_replay == first


@pytest.mark.asyncio
async def test_concurrent_first_advance_has_one_external_side_effect_owner(tmp_path: Path) -> None:
    path = tmp_path / "artifacts.sqlite3"
    knowledge = BlockingKnowledgePort()
    first_service = LearningExecutionService(
        store=LearningArtifactStore(path),
        map_service=LearningMapService(knowledge_port=knowledge),
    )
    second_service = LearningExecutionService(
        store=LearningArtifactStore(path),
        map_service=LearningMapService(knowledge_port=knowledge),
    )
    winner = asyncio.create_task(
        first_service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            context=_context(),
            expected_checkpoint_revision=0,
            idempotency_key="concurrent-winner",
        )
    )
    await knowledge.first_started.wait()
    loser_error: Exception | None = None
    try:
        await second_service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            context=_context(),
            expected_checkpoint_revision=0,
            idempotency_key="concurrent-loser",
        )
    except Exception as exc:  # public loser outcome is asserted after releasing the winner
        loser_error = exc
    finally:
        knowledge.release_first.set()
    await winner

    assert isinstance(loser_error, LearningCheckpointConflictError)
    assert knowledge.calls == 1


@pytest.mark.asyncio
async def test_binding_conflict_does_not_mutate_checkpoint_or_fencing(tmp_path: Path) -> None:
    store = LearningArtifactStore(tmp_path / "artifacts.sqlite3")
    service = LearningExecutionService(
        store=store,
        map_service=LearningMapService(knowledge_port=FakeKnowledgePort()),
    )
    first = await service.advance(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        context=_context(),
        expected_checkpoint_revision=0,
        idempotency_key="advance-initial",
    )
    before = store.checkpoint(owner_id="local", workspace_id="workspace-1", task_id=_task().task_id)

    with pytest.raises(LearningResumeConflictError):
        await service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            context=replace(_context(), capability_revision="cap-r2"),
            expected_checkpoint_revision=first.checkpoint_revision,
            idempotency_key="advance-drift",
        )

    after = store.checkpoint(owner_id="local", workspace_id="workspace-1", task_id=_task().task_id)
    assert after == before


@pytest.mark.asyncio
async def test_source_gap_conditionally_researches_and_synthesizes_web_citation(
    tmp_path: Path,
) -> None:
    evidence = EvidenceBundleItem(
        evidence_ref="wcite-1",
        kind="web_fetch",
        content="A checkpoint keeps revision-bound recovery state.",
        title="Checkpoint docs",
        source_ref="web:https://docs.example.com/checkpoint",
        canonical_url="https://docs.example.com/checkpoint",
        content_hash="sha256:web-r1",
        token_count=30,
        metadata={"fetched_at": "2026-08-25T01:00:00Z"},
    )
    plan = (
        await LearningMapService(knowledge_port=None).build(
            owner_id="local",
            task=_task(),
            parent_run_id="run-parent",
            capability_revision="cap-r1",
            catalog_revision="catalog-r1",
        )
    ).plan
    receipt = LearningResearchReceipt(
        schema_version=1,
        receipt_id="lrsearch_execution_1",
        task_id=_task().task_id,
        task_revision=_task().task_revision,
        plan_id=plan.plan_id,
        plan_revision=plan.plan_revision,
        unit_id=plan.units[0].unit_id,
        parent_run_id="run-parent",
        child_run_id="run-child",
        capability_revision=plan.capability_revision,
        source_policy_revision=plan.source_policy_revision,
        query_receipt_hash="lquery_execution_1",
        token_budget=2_000,
        max_steps=4,
        timeout_seconds=20,
        actual_token_usage=600,
        actual_tool_count=2,
        allowed_domains=("example.com",),
        freshness="current",
        risk_decision="general_education",
        terminal_status="succeeded",
        reason_code="",
        evidence=(
            LearningResearchEvidence(
                evidence_ref=evidence.evidence_ref,
                url=evidence.canonical_url,
                title=evidence.title,
                content_hash=evidence.content_hash,
                fetched_at=str(evidence.metadata["fetched_at"]),
                kind=evidence.kind,
            ),
        ),
    )
    research = FakeResearchService(
        LearningResearchOutcome(
            status="succeeded", reason_code="", receipt=receipt, evidence=(evidence,)
        )
    )
    store = LearningArtifactStore(tmp_path / "artifacts.sqlite3")
    service = LearningExecutionService(
        store=store,
        map_service=LearningMapService(knowledge_port=None),
        research_service=research,
    )

    current_revision = 0
    expected_stages = (
        "knowledge_pending",
        "source_gap",
        "research_pending",
        "research_ready",
        "synthesize_pending",
        "artifact_ready",
    )
    for index, expected_stage in enumerate(expected_stages, start=1):
        summary = await service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            context=_context(),
            expected_checkpoint_revision=current_revision,
            idempotency_key=f"research-advance-{index}",
        )
        current_revision = summary.checkpoint_revision
        assert summary.stage == expected_stage

    assert research.calls == 1
    artifact = store.read_artifact(
        owner_id="local",
        workspace_id="workspace-1",
        artifact_ref=summary.artifact_ref,
    )
    assert artifact.status == "ready"
    assert artifact.citations[0].url == "https://docs.example.com/checkpoint"
    assert artifact.citations[0].fetched_at == "2026-08-25T01:00:00Z"
    assert "https://docs.example.com/checkpoint" in artifact.content
    stored_receipt = store.read_research_receipt(
        owner_id="local",
        workspace_id="workspace-1",
        receipt_ref=artifact.research_receipt_ref,
    )
    assert stored_receipt.receipt.parent_run_id == "run-parent"
