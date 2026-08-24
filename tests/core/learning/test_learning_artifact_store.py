from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from sage_harness import KnowledgeEvidence, KnowledgeRetrievalResult

from core.learning.artifact_store import (
    LearningArtifactConflictError,
    LearningArtifactNotFoundError,
    LearningArtifactStore,
    LearningArtifactStoreError,
    LearningCheckpointConflictError,
    LearningFencingConflictError,
    LearningResumeConflictError,
    LearningResumeNotFoundError,
)
from core.learning.materials import LearningCitation, LearningMapOutcome, LearningMapService
from core.learning.research import (
    LearningResearchEvidence,
    LearningResearchReceipt,
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
        source_policy=LearningSourcePolicy(web="forbidden"),
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


async def _outcome(*, owner_id: str = "local") -> LearningMapOutcome:
    return await LearningMapService(knowledge_port=FakeKnowledgePort()).build(
        owner_id=owner_id,
        task=_task(),
        parent_run_id="run-parent",
        capability_revision="cap-r1",
        catalog_revision="catalog-r1",
    )


@pytest.mark.asyncio
async def test_artifact_is_idempotent_and_survives_process_reopen(tmp_path: Path) -> None:
    outcome = await _outcome()
    store = LearningArtifactStore(tmp_path / "learning-artifacts.sqlite3")
    checkpoint = store.begin_execution(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        plan=outcome.plan,
        lease_owner_id="writer-a",
    )

    first = store.save_artifact(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        plan=outcome.plan,
        artifact=outcome.artifact,
        citations=outcome.citations,
        idempotency_key="knowledge-map-r2",
        retention="task",
    )
    replay = store.save_artifact(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        plan=outcome.plan,
        artifact=outcome.artifact,
        citations=outcome.citations,
        idempotency_key="knowledge-map-r2",
        retention="task",
    )

    assert replay == first
    assert first.artifact_ref.startswith("sage://learning/artifacts/")
    assert first.schema_version == 1
    assert first.goal_id == "goal-1"
    assert first.goal_revision == "goal-r1"
    assert first.plan_revision == outcome.plan.plan_revision
    assert first.unit_ids == tuple(unit.unit_id for unit in outcome.plan.units)
    assert checkpoint.stage == "knowledge_pending"
    reopened = LearningArtifactStore(tmp_path / "learning-artifacts.sqlite3")
    assert (
        reopened.read_artifact(
            owner_id="local",
            workspace_id="workspace-1",
            artifact_ref=first.artifact_ref,
        )
        == first
    )
    with pytest.raises(LearningArtifactNotFoundError):
        reopened.read_artifact(
            owner_id="other",
            workspace_id="workspace-1",
            artifact_ref=first.artifact_ref,
        )

    changed = replace(
        outcome.artifact,
        content=outcome.artifact.content + "changed\n",
        content_hash="sha256:changed",
    )
    with pytest.raises(LearningArtifactConflictError) as conflict:
        store.save_artifact(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            plan=outcome.plan,
            artifact=changed,
            citations=outcome.citations,
            idempotency_key="knowledge-map-r2",
            retention="task",
        )
    assert conflict.value.code == "learning_artifact_contract_conflict"

    invalid_binding = replace(outcome.artifact, evidence_refs=("unknown-citation",))
    with pytest.raises(ValueError, match="evidence refs"):
        store.save_artifact(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            plan=outcome.plan,
            artifact=invalid_binding,
            citations=outcome.citations,
            idempotency_key="invalid-citation-binding",
            retention="task",
        )


@pytest.mark.asyncio
async def test_research_receipt_is_durable_and_scope_bound(tmp_path: Path) -> None:
    outcome = await _outcome()
    receipt = LearningResearchReceipt(
        schema_version=1,
        receipt_id="",
        owner_id="local",
        workspace_id="workspace-1",
        task_id=_task().task_id,
        task_revision=_task().task_revision,
        plan_id=outcome.plan.plan_id,
        plan_revision=outcome.plan.plan_revision,
        unit_id=outcome.plan.units[0].unit_id,
        parent_run_id="run-parent",
        child_run_id="run-child",
        capability_revision=outcome.plan.capability_revision,
        source_policy_revision=outcome.plan.source_policy_revision,
        query_receipt_hash="lquery_hash_1",
        token_budget=2_000,
        max_steps=4,
        timeout_seconds=20,
        actual_token_usage=600,
        actual_tool_count=2,
        actual_elapsed_seconds=0.25,
        allowed_domains=(),
        freshness="all",
        risk_decision="general_education",
        terminal_status="succeeded",
        reason_code="",
        evidence=(
            LearningResearchEvidence(
                evidence_ref="wcite-1",
                url="https://docs.example.com/checkpoint",
                title="Checkpoint docs",
                content_hash="sha256:web-r1",
                fetched_at="2026-08-25T01:00:00Z",
                kind="web_fetch",
            ),
        ),
    )
    receipt = replace(receipt, receipt_id=canonical_learning_research_receipt_id(receipt))
    path = tmp_path / "learning-artifacts.sqlite3"
    store = LearningArtifactStore(path)

    stored = store.save_research_receipt(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        plan=outcome.plan,
        receipt=receipt,
    )
    replay = LearningArtifactStore(path).save_research_receipt(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        plan=outcome.plan,
        receipt=receipt,
    )

    assert replay == stored
    assert stored.receipt_ref.startswith("sage://learning/research-receipts/lrsearch_")
    assert stored.receipt.query_receipt_hash == "lquery_hash_1"
    assert (
        LearningArtifactStore(path).read_research_receipt(
            owner_id="local",
            workspace_id="workspace-1",
            receipt_ref=stored.receipt_ref,
        )
        == stored
    )
    with pytest.raises(LearningResumeNotFoundError):
        store.read_research_receipt(
            owner_id="other",
            workspace_id="workspace-1",
            receipt_ref=stored.receipt_ref,
        )


@pytest.mark.asyncio
async def test_research_receipt_identity_is_scoped_across_owners(tmp_path: Path) -> None:
    first_outcome = await _outcome(owner_id="owner-a")
    second_outcome = await _outcome(owner_id="owner-b")

    def receipt_for(outcome: LearningMapOutcome) -> LearningResearchReceipt:
        receipt = LearningResearchReceipt(
            schema_version=1,
            receipt_id="",
            owner_id=outcome.plan.owner_id,
            workspace_id=outcome.plan.workspace_id,
            task_id=_task().task_id,
            task_revision=_task().task_revision,
            plan_id=outcome.plan.plan_id,
            plan_revision=outcome.plan.plan_revision,
            unit_id=outcome.plan.units[0].unit_id,
            parent_run_id="run-parent",
            child_run_id="run-child",
            capability_revision=outcome.plan.capability_revision,
            source_policy_revision=outcome.plan.source_policy_revision,
            query_receipt_hash="lquery_same_material",
            token_budget=2_000,
            max_steps=4,
            timeout_seconds=20,
            actual_token_usage=600,
            actual_tool_count=2,
            actual_elapsed_seconds=0.25,
            allowed_domains=(),
            freshness="all",
            risk_decision="general_education",
            terminal_status="succeeded",
            reason_code="",
            evidence=(),
        )
        return replace(
            receipt,
            receipt_id=canonical_learning_research_receipt_id(receipt),
        )

    store = LearningArtifactStore(tmp_path / "learning-artifacts.sqlite3")
    first = store.save_research_receipt(
        owner_id="owner-a",
        workspace_id="workspace-1",
        task=_task(),
        plan=first_outcome.plan,
        receipt=receipt_for(first_outcome),
    )
    second = store.save_research_receipt(
        owner_id="owner-b",
        workspace_id="workspace-1",
        task=_task(),
        plan=second_outcome.plan,
        receipt=receipt_for(second_outcome),
    )

    assert first.receipt_ref != second.receipt_ref


@pytest.mark.asyncio
async def test_checkpoint_cas_and_fencing_reject_stale_writer(tmp_path: Path) -> None:
    outcome = await _outcome()
    store = LearningArtifactStore(tmp_path / "learning-artifacts.sqlite3")
    first = store.begin_execution(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        plan=outcome.plan,
        lease_owner_id="writer-a",
    )
    second = store.acquire_lease(
        owner_id="local",
        workspace_id="workspace-1",
        task_id="ltask-1",
        lease_owner_id="writer-b",
    )
    assert second.fencing_token > first.fencing_token

    with pytest.raises(LearningFencingConflictError) as stale:
        store.advance_checkpoint(
            owner_id="local",
            workspace_id="workspace-1",
            task_id="ltask-1",
            expected_checkpoint_revision=first.checkpoint_revision,
            lease_owner_id="writer-a",
            fencing_token=first.fencing_token,
            stage="knowledge_ready",
            next_action="synthesize",
            evidence_count=1,
            citation_count=1,
        )
    assert stale.value.code == "learning_resume_fencing_conflict"

    advanced = store.advance_checkpoint(
        owner_id="local",
        workspace_id="workspace-1",
        task_id="ltask-1",
        expected_checkpoint_revision=second.checkpoint_revision,
        lease_owner_id="writer-b",
        fencing_token=second.fencing_token,
        stage="knowledge_ready",
        next_action="synthesize",
        evidence_count=1,
        citation_count=1,
    )
    assert advanced.checkpoint_revision == first.checkpoint_revision + 1

    with pytest.raises(LearningCheckpointConflictError):
        store.advance_checkpoint(
            owner_id="local",
            workspace_id="workspace-1",
            task_id="ltask-1",
            expected_checkpoint_revision=second.checkpoint_revision,
            lease_owner_id="writer-b",
            fencing_token=second.fencing_token,
            stage="artifact_ready",
            next_action="review_artifact",
        )

    reopened = LearningArtifactStore(tmp_path / "learning-artifacts.sqlite3")
    third = reopened.acquire_lease(
        owner_id="local",
        workspace_id="workspace-1",
        task_id="ltask-1",
        lease_owner_id="writer-c",
    )
    assert third.fencing_token == second.fencing_token + 1
    with pytest.raises(LearningFencingConflictError):
        store.advance_checkpoint(
            owner_id="local",
            workspace_id="workspace-1",
            task_id="ltask-1",
            expected_checkpoint_revision=advanced.checkpoint_revision,
            lease_owner_id="writer-b",
            fencing_token=second.fencing_token,
            stage="artifact_ready",
            next_action="review_artifact",
        )

    with pytest.raises(LearningResumeNotFoundError):
        reopened.acquire_lease(
            owner_id="other",
            workspace_id="workspace-1",
            task_id="ltask-1",
            lease_owner_id="writer-other",
        )


@pytest.mark.asyncio
async def test_resume_is_browser_safe_and_fails_on_revision_drift(tmp_path: Path) -> None:
    outcome = await _outcome()
    store = LearningArtifactStore(tmp_path / "learning-artifacts.sqlite3")
    checkpoint = store.begin_execution(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        plan=outcome.plan,
        lease_owner_id="writer-a",
    )
    artifact = store.save_artifact(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        plan=outcome.plan,
        artifact=outcome.artifact,
        citations=outcome.citations,
        idempotency_key="knowledge-map-r2",
        retention="task",
    )
    store.advance_checkpoint(
        owner_id="local",
        workspace_id="workspace-1",
        task_id="ltask-1",
        expected_checkpoint_revision=checkpoint.checkpoint_revision,
        lease_owner_id="writer-a",
        fencing_token=checkpoint.fencing_token,
        stage="artifact_ready",
        next_action="review_artifact",
        evidence_count=1,
        citation_count=1,
        artifact_ref=artifact.artifact_ref,
    )

    summary = store.resume(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        capability_revision="cap-r1",
    )
    assert summary.stage == "artifact_ready"
    assert summary.artifact_ref == artifact.artifact_ref
    assert summary.artifact is not None
    assert summary.artifact.content_hash == outcome.artifact.content_hash
    assert outcome.artifact.content not in repr(summary)
    assert outcome.citations[0].content not in repr(summary)
    assert "Checkpoint stores only" not in repr(summary)

    drifted_task = replace(_task(), task_revision=3)
    with pytest.raises(LearningResumeConflictError) as task_drift:
        store.resume(
            owner_id="local",
            workspace_id="workspace-1",
            task=drifted_task,
            capability_revision="cap-r1",
        )
    assert task_drift.value.code == "learning_resume_revision_conflict"

    with pytest.raises(LearningResumeConflictError):
        store.resume(
            owner_id="local",
            workspace_id="workspace-1",
            task=_task(),
            capability_revision="cap-r2",
        )

    source_drift = replace(
        _task(),
        source_policy=LearningSourcePolicy(web="allowed_when_insufficient"),
    )
    with pytest.raises(LearningResumeConflictError):
        store.resume(
            owner_id="local",
            workspace_id="workspace-1",
            task=source_drift,
            capability_revision="cap-r1",
        )


def test_citation_payload_type_is_not_required_by_checkpoint() -> None:
    citation = LearningCitation(
        citation_id="kcite-1",
        title="title",
        content="large body",
        content_hash="sha256:body",
        page_revision="page-r1",
        source_revision="source-r1",
    )
    assert "large body" in citation.content


@pytest.mark.asyncio
async def test_reopen_rejects_tampered_plan_identity(tmp_path: Path) -> None:
    path = tmp_path / "learning-artifacts.sqlite3"
    outcome = await _outcome()
    store = LearningArtifactStore(path)
    store.begin_execution(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        plan=outcome.plan,
        lease_owner_id="writer-a",
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE learning_plans SET plan_hash = 'sha256:tampered' WHERE task_id = ?",
            (_task().task_id,),
        )

    with pytest.raises(LearningArtifactStoreError) as error:
        LearningArtifactStore(path).load_plan(
            owner_id="local",
            workspace_id="workspace-1",
            task_id=_task().task_id,
        )
    assert error.value.code == "learning_persistence_integrity_error"


@pytest.mark.asyncio
async def test_reopen_rejects_tampered_unit_identity(tmp_path: Path) -> None:
    path = tmp_path / "learning-artifacts.sqlite3"
    outcome = await _outcome()
    store = LearningArtifactStore(path)
    store.begin_execution(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        plan=outcome.plan,
        lease_owner_id="writer-a",
    )
    with sqlite3.connect(path) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM learning_plans WHERE task_id = ?",
                (_task().task_id,),
            ).fetchone()[0]
        )
        payload["units"][0]["unit_id"] = "lunit_tampered"
        connection.execute(
            "UPDATE learning_plans SET payload_json = ? WHERE task_id = ?",
            (json.dumps(payload), _task().task_id),
        )

    with pytest.raises(LearningArtifactStoreError) as error:
        LearningArtifactStore(path).load_plan(
            owner_id="local",
            workspace_id="workspace-1",
            task_id=_task().task_id,
        )
    assert error.value.code == "learning_persistence_integrity_error"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("content", "tampered body"),
        ("goal_id", ""),
        ("unit_ids_json", "[]"),
        ("citations_json", "[]"),
    ],
)
async def test_reopen_quarantines_tampered_or_legacy_empty_artifact_identity(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    path = tmp_path / "learning-artifacts.sqlite3"
    outcome = await _outcome()
    store = LearningArtifactStore(path)
    artifact = store.save_artifact(
        owner_id="local",
        workspace_id="workspace-1",
        task=_task(),
        plan=outcome.plan,
        artifact=outcome.artifact,
        citations=outcome.citations,
        idempotency_key="knowledge-map-r2",
        retention="task",
    )
    assert column in {"content", "goal_id", "unit_ids_json", "citations_json"}
    with sqlite3.connect(path) as connection:
        connection.execute(
            f"UPDATE learning_artifacts SET {column} = ? WHERE artifact_ref = ?",
            (value, artifact.artifact_ref),
        )

    with pytest.raises(LearningArtifactStoreError) as error:
        LearningArtifactStore(path).read_artifact(
            owner_id="local",
            workspace_id="workspace-1",
            artifact_ref=artifact.artifact_ref,
        )
    assert error.value.code == "learning_persistence_integrity_error"
