from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from sage_harness import (
    EvidenceBundle,
    EvidenceBundleItem,
    KnowledgeEvidence,
    KnowledgeRetrievalResult,
    SubagentRequest,
    SubagentResult,
    SubagentToolConfig,
)

from core.learning.artifact_store import (
    LearningArtifactStore,
    LearningArtifactStoreError,
    LearningCheckpointConflictError,
    LearningResumeConflictError,
)
from core.learning.execution import LearningExecutionContext, LearningExecutionService
from core.learning.materials import LearningMapService
from core.learning.research import (
    LearningResearchEvidence,
    LearningResearchOutcome,
    LearningResearchReceipt,
    LearningResearchService,
    canonical_learning_research_receipt_id,
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


class CountingKnowledgePort(FakeKnowledgePort):
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, *args: object, **kwargs: object) -> KnowledgeRetrievalResult:
        self.calls += 1
        return await super().search(*args, **kwargs)  # type: ignore[arg-type]


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


class CancelOnceMapService(LearningMapService):
    def __init__(self) -> None:
        super().__init__(knowledge_port=FakeKnowledgePort())
        self.started = asyncio.Event()

    async def build(self, **kwargs: object):  # type: ignore[no-untyped-def]
        self.started.set()
        await asyncio.Future()


class FailOnceMapService(LearningMapService):
    def __init__(self) -> None:
        super().__init__(knowledge_port=FakeKnowledgePort())
        self.failed = False

    async def build(self, **kwargs: object):  # type: ignore[no-untyped-def]
        if not self.failed:
            self.failed = True
            raise RuntimeError("injected map failure")
        return await super().build(**kwargs)  # type: ignore[arg-type]


class ConflictingKnowledgePort(FakeKnowledgePort):
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
            used_tokens=120,
            omitted_count=0,
            evidence=(
                KnowledgeEvidence(
                    citation_id="kcite-conflict-a",
                    content="Checkpoint state includes the full generated body.",
                    page_revision="page-a",
                    source_revision="source-a",
                    metadata={"title": "Source A", "conflict_group": "checkpoint-body"},
                ),
                KnowledgeEvidence(
                    citation_id="kcite-conflict-b",
                    content="Checkpoint state excludes the full generated body.",
                    page_revision="page-b",
                    source_revision="source-b",
                    metadata={"title": "Source B", "conflict_group": "checkpoint-body"},
                ),
            ),
        )


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


class NoopResearchExecutor:
    async def execute(self, request: SubagentRequest, progress=None) -> SubagentResult:  # type: ignore[no-untyped-def]
        raise AssertionError("gate failure must not create a child")

    async def cancel(self, child_run_id: str, reason: str = "parent_cancelled") -> None:
        return None


class EmptyEvidencePort:
    available = True

    async def read(self, *args: object, **kwargs: object) -> EvidenceBundle:
        return EvidenceBundle(status="no_evidence")


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
async def test_cancelled_and_failed_same_key_requests_can_retry(tmp_path: Path) -> None:
    path = tmp_path / "artifacts.sqlite3"
    cancelled_map = CancelOnceMapService()
    cancelled_service = LearningExecutionService(
        store=LearningArtifactStore(path),
        map_service=cancelled_map,
    )
    pending = asyncio.create_task(
        cancelled_service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            context=_context(),
            expected_checkpoint_revision=0,
            idempotency_key="cancelled-retry",
        )
    )
    await cancelled_map.started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    retried = await LearningExecutionService(
        store=LearningArtifactStore(path),
        map_service=LearningMapService(knowledge_port=FakeKnowledgePort()),
    ).advance(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        context=_context(),
        expected_checkpoint_revision=0,
        idempotency_key="cancelled-retry",
    )
    assert retried.checkpoint_revision == 1

    failed_path = tmp_path / "failed.sqlite3"
    failed_service = LearningExecutionService(
        store=LearningArtifactStore(failed_path),
        map_service=FailOnceMapService(),
    )
    with pytest.raises(RuntimeError, match="injected map failure"):
        await failed_service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            context=_context(),
            expected_checkpoint_revision=0,
            idempotency_key="failed-retry",
        )
    recovered = await failed_service.advance(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        context=_context(),
        expected_checkpoint_revision=0,
        idempotency_key="failed-retry",
    )
    assert recovered.stage == "knowledge_pending"


def test_expired_request_supports_fenced_takeover_across_store_instances(tmp_path: Path) -> None:
    path = tmp_path / "artifacts.sqlite3"
    first_store = LearningArtifactStore(path)
    first = first_store.claim_advance_request(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        capability_revision="cap-r1",
        catalog_revision="catalog-r1",
        expected_checkpoint_revision=0,
        idempotency_key="orphaned-request",
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE learning_advance_requests SET lease_expires_at = ?",
            ("2000-01-01T00:00:00Z",),
        )

    second_store = LearningArtifactStore(path)
    takeover = second_store.claim_advance_request(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        capability_revision="cap-r1",
        catalog_revision="catalog-r1",
        expected_checkpoint_revision=0,
        idempotency_key="orphaned-request",
    )
    assert takeover.fencing_token == first.fencing_token + 1
    assert takeover.lease_owner_id != first.lease_owner_id

    first_store.fail_advance_request(
        owner_id="local",
        workspace_id="workspace-1",
        task_id=_task().task_id,
        claim=first,
        error_code="stale-owner",
    )
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT status, fencing_token FROM learning_advance_requests"
        ).fetchone()
    assert row == ("running", takeover.fencing_token)


def test_expired_orphan_allows_new_key_but_unexpired_request_blocks_loser(tmp_path: Path) -> None:
    path = tmp_path / "artifacts.sqlite3"
    store = LearningArtifactStore(path)
    store.claim_advance_request(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        capability_revision="cap-r1",
        catalog_revision="catalog-r1",
        expected_checkpoint_revision=0,
        idempotency_key="first-key",
    )
    with pytest.raises(LearningCheckpointConflictError):
        LearningArtifactStore(path).claim_advance_request(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            capability_revision="cap-r1",
            catalog_revision="catalog-r1",
            expected_checkpoint_revision=0,
            idempotency_key="second-key",
        )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE learning_advance_requests SET lease_expires_at = ?",
            ("2000-01-01T00:00:00Z",),
        )
    claimed = LearningArtifactStore(path).claim_advance_request(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        capability_revision="cap-r1",
        catalog_revision="catalog-r1",
        expected_checkpoint_revision=0,
        idempotency_key="second-key",
    )
    assert claimed.replay is None


@pytest.mark.asyncio
async def test_succeeded_replay_rejects_response_tamper_and_old_schema(tmp_path: Path) -> None:
    path = tmp_path / "artifacts.sqlite3"
    service = LearningExecutionService(
        store=LearningArtifactStore(path),
        map_service=LearningMapService(knowledge_port=FakeKnowledgePort()),
    )
    await service.advance(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        context=_context(),
        expected_checkpoint_revision=0,
        idempotency_key="tamper-replay",
    )
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE learning_advance_requests SET response_json = '{}'")
    with pytest.raises(LearningArtifactStoreError) as tampered:
        await service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            context=_context(),
            expected_checkpoint_revision=0,
            idempotency_key="tamper-replay",
        )
    assert tampered.value.code == "learning_persistence_integrity_error"

    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE learning_advance_requests SET schema_version = 0")
    with pytest.raises(LearningArtifactStoreError) as legacy:
        await service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            context=_context(),
            expected_checkpoint_revision=0,
            idempotency_key="tamper-replay",
        )
    assert legacy.value.code == "learning_persistence_integrity_error"


@pytest.mark.asyncio
async def test_same_key_recovers_checkpoint_committed_before_request_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "artifacts.sqlite3"
    knowledge = CountingKnowledgePort()
    store = LearningArtifactStore(path)
    service = LearningExecutionService(
        store=store,
        map_service=LearningMapService(knowledge_port=knowledge),
    )
    original_complete = store.complete_advance_request

    def crash_before_completion(**kwargs: object) -> None:
        raise RuntimeError("injected completion crash")

    monkeypatch.setattr(store, "complete_advance_request", crash_before_completion)
    with pytest.raises(RuntimeError, match="injected completion crash"):
        await service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            context=_context(),
            expected_checkpoint_revision=0,
            idempotency_key="commit-before-complete",
        )
    assert knowledge.calls == 1
    monkeypatch.setattr(store, "complete_advance_request", original_complete)

    replay = await service.advance(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        context=_context(),
        expected_checkpoint_revision=0,
        idempotency_key="commit-before-complete",
    )
    assert replay.checkpoint_revision == 1
    assert knowledge.calls == 1


@pytest.mark.asyncio
async def test_conflicting_knowledge_preserves_citations_and_never_becomes_ready(
    tmp_path: Path,
) -> None:
    store = LearningArtifactStore(tmp_path / "artifacts.sqlite3")
    service = LearningExecutionService(
        store=store,
        map_service=LearningMapService(knowledge_port=ConflictingKnowledgePort()),
    )
    first = await service.advance(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        context=_context(),
        expected_checkpoint_revision=0,
        idempotency_key="conflict-1",
    )
    second = await service.advance(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        context=_context(),
        expected_checkpoint_revision=first.checkpoint_revision,
        idempotency_key="conflict-2",
    )
    artifact = store.read_artifact(
        owner_id="local",
        workspace_id="workspace-1",
        artifact_ref=second.artifact_ref,
    )

    assert second.stage == "source_gap"
    assert artifact.status == "unverified"
    assert tuple(item.evidence_ref for item in artifact.citations) == (
        "kcite-conflict-a",
        "kcite-conflict-b",
    )


@pytest.mark.asyncio
async def test_research_gate_failure_is_persisted_without_creating_child(tmp_path: Path) -> None:
    path = tmp_path / "artifacts.sqlite3"
    store = LearningArtifactStore(path)
    research = LearningResearchService(
        subagent_executor=NoopResearchExecutor(),
        subagent_config=SubagentToolConfig(),
        evidence_bundle_port=EmptyEvidencePort(),
    )
    service = LearningExecutionService(
        store=store,
        map_service=LearningMapService(knowledge_port=None),
        research_service=research,
    )
    current_revision = 0
    current = None
    for index in range(1, 5):
        current = await service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            context=_context(),
            expected_checkpoint_revision=current_revision,
            idempotency_key=f"gate-receipt-{index}",
        )
        current_revision = current.checkpoint_revision

    assert current is not None
    assert current.stage == "blocked"
    assert current.blocking_reason == "learning_research_capability_unavailable"
    with sqlite3.connect(path) as connection:
        rows = connection.execute("SELECT payload_json FROM learning_research_receipts").fetchall()
    assert len(rows) == 1
    payload = json.loads(rows[0][0])
    assert payload["child_run_id"] == ""
    assert payload["terminal_status"] == "not_started"
    assert payload["reason_code"] == "learning_research_capability_unavailable"


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
        receipt_id="",
        owner_id="local",
        workspace_id="workspace-1",
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
        actual_elapsed_seconds=0.25,
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
    receipt = replace(receipt, receipt_id=canonical_learning_research_receipt_id(receipt))
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


@pytest.mark.asyncio
async def test_research_merges_knowledge_conflicts_and_never_claims_ready(
    tmp_path: Path,
) -> None:
    task = _task()
    initial = await LearningMapService(knowledge_port=ConflictingKnowledgePort()).build(
        owner_id="local",
        task=task,
        parent_run_id="run-parent",
        capability_revision="cap-r1",
        catalog_revision="catalog-r1",
    )
    web = EvidenceBundleItem(
        evidence_ref="wcite-conflict-web",
        kind="web_fetch",
        content="Current Web documentation excludes full generated bodies from checkpoints.",
        title="Current checkpoint docs",
        source_ref="web:https://docs.example.com/checkpoint",
        canonical_url="https://docs.example.com/checkpoint",
        content_hash="sha256:web-conflict-r1",
        token_count=30,
        metadata={
            "fetched_at": "2026-08-25T01:00:00Z",
            "conflict_group": "checkpoint-body",
        },
    )
    receipt = LearningResearchReceipt(
        schema_version=1,
        receipt_id="",
        owner_id="local",
        workspace_id="workspace-1",
        task_id=task.task_id,
        task_revision=task.task_revision,
        plan_id=initial.plan.plan_id,
        plan_revision=initial.plan.plan_revision,
        unit_id=initial.plan.units[0].unit_id,
        parent_run_id="run-parent",
        child_run_id="run-child-conflict",
        capability_revision=initial.plan.capability_revision,
        source_policy_revision=initial.plan.source_policy_revision,
        query_receipt_hash="lquery_execution_conflict",
        token_budget=2_000,
        max_steps=4,
        timeout_seconds=20,
        actual_token_usage=600,
        actual_tool_count=2,
        actual_elapsed_seconds=0.25,
        allowed_domains=("example.com",),
        freshness="current",
        risk_decision="general_education",
        terminal_status="succeeded",
        reason_code="learning_research_conflict",
        evidence=(
            LearningResearchEvidence(
                evidence_ref=web.evidence_ref,
                url=web.canonical_url,
                title=web.title,
                content_hash=web.content_hash,
                fetched_at=str(web.metadata["fetched_at"]),
                kind=web.kind,
            ),
        ),
    )
    receipt = replace(receipt, receipt_id=canonical_learning_research_receipt_id(receipt))
    store = LearningArtifactStore(tmp_path / "artifacts.sqlite3")
    service = LearningExecutionService(
        store=store,
        map_service=LearningMapService(knowledge_port=ConflictingKnowledgePort()),
        research_service=FakeResearchService(
            LearningResearchOutcome(
                status="succeeded",
                reason_code="learning_research_conflict",
                receipt=receipt,
                evidence=(web,),
            )
        ),
    )

    revision = 0
    for index in range(1, 5):
        summary = await service.advance(
            owner_id="local",
            workspace_id="workspace-1",
            task=task,
            context=_context(),
            expected_checkpoint_revision=revision,
            idempotency_key=f"merged-conflict-{index}",
        )
        revision = summary.checkpoint_revision

    artifact = store.read_artifact(
        owner_id="local",
        workspace_id="workspace-1",
        artifact_ref=summary.artifact_ref,
    )
    assert artifact.status == "unverified"
    assert tuple(item.evidence_ref for item in artifact.citations) == (
        "kcite-conflict-a",
        "kcite-conflict-b",
        "wcite-conflict-web",
    )
    assert "证据冲突未解决" in artifact.content
    assert "已由当前 revision 的证据支持" not in artifact.content
