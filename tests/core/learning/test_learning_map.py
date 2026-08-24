from __future__ import annotations

from dataclasses import replace

import pytest
from sage_harness import KnowledgeEvidence, KnowledgeRetrievalResult

from core.learning.materials import LearningMapService
from core.learning.tasks import (
    LearningClarification,
    LearningLearnerProfile,
    LearningSourcePolicy,
    LearningTask,
    source_policy_revision,
)


class FakeKnowledgePort:
    workspace_id = "knowledge-workspace"
    available = True

    def __init__(self, result: KnowledgeRetrievalResult) -> None:
        self.result = result
        self.calls = 0

    async def search(
        self,
        query: str,
        *,
        workspace_id: str,
        token_budget: int,
        top_k: int = 8,
    ) -> KnowledgeRetrievalResult:
        self.calls += 1
        assert workspace_id == self.workspace_id
        assert 1 <= top_k <= 20
        assert token_budget >= 256
        return self.result


def _task(*, policy: LearningSourcePolicy | None = None, financial: bool = False) -> LearningTask:
    topic = "学习指数基金风险" if financial else "学习 Sage checkpoint 与 resume"
    return LearningTask(
        version=1,
        workspace_id="workspace-1",
        task_id="ltask-1",
        task_revision=3,
        template_id="general_learning",
        topic=topic,
        desired_outcome="能够解释恢复边界",
        learner_profile=LearningLearnerProfile(
            starting_level="beginner",
            time_budget_minutes_per_week=180,
        ),
        source_policy=policy or LearningSourcePolicy(web="forbidden"),
        risk_class="financial_education" if financial else "general_education",
        risk_notice=(
            "仅提供金融与投资教育，不提供个性化证券买卖建议或收益承诺。" if financial else None
        ),
        clarification=LearningClarification((), (), True),
        learning_plan_id=None,
        learning_plan_hash=None,
        dag_hash=None,
        learning_goal_ref={"goal_id": "goal-1", "goal_revision": "goal-rev-2"},
        status="active",
        created_at="2026-08-25T00:00:00Z",
        updated_at="2026-08-25T00:00:01Z",
    )


def _result(*evidence: KnowledgeEvidence) -> KnowledgeRetrievalResult:
    return KnowledgeRetrievalResult(
        query="checkpoint resume",
        workspace_id="knowledge-workspace",
        status="evidence_found" if evidence else "no_evidence",
        token_budget=3_000,
        used_tokens=100 * len(evidence),
        omitted_count=0,
        evidence=tuple(evidence),
    )


def _evidence(*, page_revision: str = "page-rev-1") -> KnowledgeEvidence:
    return KnowledgeEvidence(
        citation_id="kcite_checkpoint",
        content="Checkpoint 保存恢复位置；Artifact 保存大正文。",
        page_revision=page_revision,
        source_revision="source-rev-4",
        metadata={"title": "Sage 恢复边界", "source_relative_path": "sage.md"},
    )


@pytest.mark.asyncio
async def test_knowledge_first_map_has_stable_identity_and_grounded_citations() -> None:
    task = _task()
    port = FakeKnowledgePort(_result(_evidence()))
    service = LearningMapService(knowledge_port=port)

    first = await service.build(
        owner_id="local",
        task=task,
        parent_run_id="run-learning-1",
        capability_revision="cap-rev-2",
        catalog_revision="catalog-rev-1",
    )
    replay = await service.build(
        owner_id="local",
        task=task,
        parent_run_id="run-learning-1",
        capability_revision="cap-rev-2",
        catalog_revision="catalog-rev-1",
    )

    assert first.plan == replay.plan
    assert first.artifact.content_hash == replay.artifact.content_hash
    assert first.plan.plan_id.startswith("lplan_")
    assert first.plan.plan_hash.startswith("sha256:")
    assert first.plan.source_policy_revision == source_policy_revision(task.source_policy)
    assert first.plan.units[0].unit_id.startswith("lunit_")
    assert first.plan.units[0].status == "grounded"
    assert first.plan.units[0].evidence_refs == ("kcite_checkpoint",)
    assert first.artifact.status == "ready"
    assert first.artifact.citation_count == 1
    assert "[kcite_checkpoint]" in first.artifact.content
    assert "掌握" not in first.artifact.content
    assert port.calls == 2

    changed_policy = replace(
        task, source_policy=LearningSourcePolicy(web="allowed_when_insufficient")
    )
    changed = await service.build(
        owner_id="local",
        task=changed_policy,
        parent_run_id="run-learning-1",
        capability_revision="cap-rev-2",
        catalog_revision="catalog-rev-1",
    )
    assert changed.plan.plan_id != first.plan.plan_id


@pytest.mark.asyncio
async def test_plan_and_unit_identity_are_owner_scoped() -> None:
    service = LearningMapService(knowledge_port=FakeKnowledgePort(_result(_evidence())))

    owner_a = await service.build(
        owner_id="owner-a",
        task=_task(),
        parent_run_id="run-learning-1",
        capability_revision="cap-rev-2",
        catalog_revision="catalog-rev-1",
    )
    owner_b = await service.build(
        owner_id="owner-b",
        task=_task(),
        parent_run_id="run-learning-1",
        capability_revision="cap-rev-2",
        catalog_revision="catalog-rev-1",
    )

    assert owner_a.plan.owner_id == "owner-a"
    assert owner_b.plan.owner_id == "owner-b"
    assert owner_a.plan.plan_id != owner_b.plan.plan_id
    assert owner_a.plan.units[0].unit_id != owner_b.plan.units[0].unit_id


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence", [None, _evidence(page_revision="")])
async def test_zero_or_unrevisioned_knowledge_fails_closed_without_citation(evidence) -> None:
    port = FakeKnowledgePort(_result(*(() if evidence is None else (evidence,))))
    outcome = await LearningMapService(knowledge_port=port).build(
        owner_id="local",
        task=_task(),
        parent_run_id="run-learning-1",
        capability_revision="cap-rev-2",
        catalog_revision="catalog-rev-1",
    )

    assert outcome.plan.units[0].status == "source_gap"
    assert outcome.plan.units[0].evidence_refs == ()
    assert outcome.artifact.status == "source_gap"
    assert outcome.artifact.citation_count == 0
    assert "kcite_checkpoint" not in outcome.artifact.content


@pytest.mark.asyncio
async def test_disabled_knowledge_does_not_call_port() -> None:
    port = FakeKnowledgePort(_result(_evidence()))
    outcome = await LearningMapService(knowledge_port=port).build(
        owner_id="local",
        task=_task(policy=LearningSourcePolicy(knowledge="disabled")),
        parent_run_id="run-learning-1",
        capability_revision="cap-rev-2",
        catalog_revision="catalog-rev-1",
    )

    assert port.calls == 0
    assert outcome.artifact.status == "source_gap"
    assert outcome.gap_reason == "knowledge_disabled"


@pytest.mark.asyncio
async def test_financial_map_is_education_only() -> None:
    outcome = await LearningMapService(
        knowledge_port=FakeKnowledgePort(_result(_evidence()))
    ).build(
        owner_id="local",
        task=_task(financial=True),
        parent_run_id="run-learning-1",
        capability_revision="cap-rev-2",
        catalog_revision="catalog-rev-1",
    )

    assert "不提供个性化证券买卖建议" in outcome.artifact.content
    assert "买入" not in outcome.artifact.content
    assert "收益承诺" in outcome.artifact.content
