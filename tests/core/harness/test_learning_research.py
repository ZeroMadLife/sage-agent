from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import pytest
from sage_harness import (
    EvidenceBundle,
    EvidenceBundleItem,
    SubagentProfile,
    SubagentRequest,
    SubagentResult,
    SubagentToolConfig,
)

from core.learning.materials import LearningMapService
from core.learning.research import LearningResearchService
from core.learning.tasks import (
    LearningClarification,
    LearningLearnerProfile,
    LearningSourcePolicy,
    LearningTask,
)


class FakeExecutor:
    def __init__(self, result: SubagentResult) -> None:
        self.result = result
        self.requests: list[SubagentRequest] = []

    async def execute(self, request: SubagentRequest, progress=None) -> SubagentResult:  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return replace(self.result, child_run_id=request.child_run_id)

    async def cancel(self, child_run_id: str, reason: str = "parent_cancelled") -> None:
        return None


class FakeEvidencePort:
    available = True

    def __init__(self, bundle: EvidenceBundle) -> None:
        self.bundle = bundle
        self.calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    async def read(
        self,
        thread_id: str,
        parent_run_id: str,
        *,
        child_run_ids: Sequence[str],
        evidence_refs: Sequence[str],
        token_budget: int,
    ) -> EvidenceBundle:
        self.calls.append((tuple(child_run_ids), tuple(evidence_refs)))
        return self.bundle


def _task(policy: LearningSourcePolicy | None = None) -> LearningTask:
    return LearningTask(
        version=1,
        workspace_id="workspace-1",
        task_id="ltask-1",
        task_revision=2,
        template_id="general_learning",
        topic="学习 Sage checkpoint",
        desired_outcome="能够解释恢复边界",
        learner_profile=LearningLearnerProfile("beginner", 180),
        source_policy=policy
        or LearningSourcePolicy(
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
        learning_goal_ref={"goal_id": "goal-1", "goal_revision": "goal-rev-1"},
        status="active",
        created_at="2026-08-25T00:00:00Z",
        updated_at="2026-08-25T00:00:01Z",
    )


async def _plan(task: LearningTask):  # type: ignore[no-untyped-def]
    return (
        await LearningMapService(knowledge_port=None).build(
            task=task,
            parent_run_id="run-parent",
            capability_revision="cap-rev-1",
            catalog_revision="catalog-rev-1",
        )
    ).plan


def _config() -> SubagentToolConfig:
    return SubagentToolConfig(
        allowed_types=frozenset({"research"}),
        profiles=(
            SubagentProfile(
                name="research",
                tool_scope=("search_web", "fetch_web", "knowledge_search"),
                token_budget=2_000,
                timeout_seconds=20,
                max_steps=4,
            ),
        ),
    )


def _result(
    status: str = "succeeded",
    *,
    refs: tuple[str, ...] = ("wcite-1",),
    error_code: str = "",
) -> SubagentResult:
    return SubagentResult(
        child_run_id="placeholder",
        status=status,  # type: ignore[arg-type]
        result="research result",
        result_ref="subagent://thread/result",
        error_code=error_code,
        evidence_refs=refs,
        token_usage=600,
        model_calls=1,
        tool_count=2,
    )


def _item(
    *,
    ref: str = "wcite-1",
    url: str = "https://docs.example.com/checkpoint",
    content_hash: str = "web-content-1",
    fetched_at: str = "2026-08-25T00:01:00Z",
) -> EvidenceBundleItem:
    return EvidenceBundleItem(
        evidence_ref=ref,
        kind="web_fetch",
        content="Checkpoint uses a durable revision-bound resume state.",
        title="Sage checkpoint docs",
        source_ref=f"web:{url}",
        canonical_url=url,
        content_hash=content_hash,
        token_count=40,
        metadata={"fetched_at": fetched_at},
    )


def _bundle(*items: EvidenceBundleItem) -> EvidenceBundle:
    return EvidenceBundle(
        status="evidence_found" if items else "no_evidence",
        items=tuple(items),
        requested_refs=tuple(item.evidence_ref for item in items),
        token_budget=2_000,
        used_tokens=40 * len(items),
    )


@pytest.mark.asyncio
async def test_conditional_research_binds_child_and_traceable_receipt() -> None:
    task = _task()
    executor = FakeExecutor(_result())
    evidence = FakeEvidencePort(_bundle(_item()))
    service = LearningResearchService(
        subagent_executor=executor,
        subagent_config=_config(),
        evidence_bundle_port=evidence,
    )

    outcome = await service.run(
        task=task,
        plan=await _plan(task),
        unit_id=(await _plan(task)).units[0].unit_id,
        thread_id="session-1",
        parent_run_id="run-parent",
        workspace_path="/workspace",
        capability_revision="cap-rev-1",
        allowed_capabilities=frozenset({"web:search", "web:fetch"}),
        evidence_sufficient=False,
        remaining_token_budget=2_000,
    )

    assert outcome.status == "succeeded"
    assert outcome.reason_code == ""
    assert len(executor.requests) == 1
    request = executor.requests[0]
    assert request.parent_run_id == "run-parent"
    assert request.subagent_type == "research"
    assert request.tool_scope == ("search_web", "fetch_web")
    assert request.token_budget == 2_000
    assert request.query_fingerprints == (outcome.receipt.query_receipt_hash,)
    assert outcome.receipt.capability_revision == "cap-rev-1"
    assert outcome.receipt.parent_run_id == "run-parent"
    assert outcome.receipt.actual_token_usage == 600
    assert outcome.receipt.evidence[0].url == "https://docs.example.com/checkpoint"
    assert outcome.receipt.evidence[0].title == "Sage checkpoint docs"
    assert outcome.receipt.evidence[0].content_hash == "web-content-1"
    assert outcome.receipt.evidence[0].fetched_at == "2026-08-25T00:01:00Z"
    assert "durable revision-bound" not in repr(outcome.receipt)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "sufficient", "capabilities", "budget", "reason"),
    [
        (
            LearningSourcePolicy(web="forbidden"),
            False,
            frozenset({"web:search"}),
            2_000,
            "learning_research_policy_forbidden",
        ),
        (LearningSourcePolicy(), True, frozenset({"web:search"}), 2_000, "evidence_sufficient"),
        (
            LearningSourcePolicy(),
            False,
            frozenset(),
            2_000,
            "learning_research_capability_unavailable",
        ),
        (
            LearningSourcePolicy(),
            False,
            frozenset({"web:search"}),
            128,
            "learning_research_budget_exhausted",
        ),
    ],
)
async def test_research_gate_does_not_create_child(
    policy: LearningSourcePolicy,
    sufficient: bool,
    capabilities: frozenset[str],
    budget: int,
    reason: str,
) -> None:
    task = _task(policy)
    executor = FakeExecutor(_result())
    service = LearningResearchService(
        subagent_executor=executor,
        subagent_config=_config(),
        evidence_bundle_port=FakeEvidencePort(_bundle(_item())),
    )
    plan = await _plan(task)

    outcome = await service.run(
        task=task,
        plan=plan,
        unit_id=plan.units[0].unit_id,
        thread_id="session-1",
        parent_run_id="run-parent",
        workspace_path="/workspace",
        capability_revision="cap-rev-1",
        allowed_capabilities=capabilities,
        evidence_sufficient=sufficient,
        remaining_token_budget=budget,
    )

    assert outcome.reason_code == reason
    assert executor.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "bundle", "reason"),
    [
        (_result("timed_out"), _bundle(), "learning_research_timeout"),
        (
            _result("failed", error_code="provider_unavailable"),
            _bundle(),
            "learning_research_provider_unavailable",
        ),
        (_result(refs=()), _bundle(), "learning_research_no_evidence"),
        (_result(), _bundle(), "learning_research_no_evidence"),
        (
            _result(),
            _bundle(_item(url="https://other.test/docs")),
            "learning_research_domain_forbidden",
        ),
        (_result(), _bundle(_item(fetched_at="")), "learning_research_freshness_unverified"),
        (
            _result(),
            _bundle(_item(fetched_at="2020-01-01T00:00:00Z")),
            "learning_research_freshness_unverified",
        ),
        (
            _result(refs=("wcite-1", "wcite-2")),
            _bundle(
                _item(ref="wcite-1", content_hash="hash-a"),
                _item(ref="wcite-2", content_hash="hash-b"),
            ),
            "learning_research_conflict",
        ),
    ],
)
async def test_research_failures_are_deterministic(
    result: SubagentResult,
    bundle: EvidenceBundle,
    reason: str,
) -> None:
    task = _task()
    service = LearningResearchService(
        subagent_executor=FakeExecutor(result),
        subagent_config=_config(),
        evidence_bundle_port=FakeEvidencePort(bundle),
    )
    plan = await _plan(task)

    outcome = await service.run(
        task=task,
        plan=plan,
        unit_id=plan.units[0].unit_id,
        thread_id="session-1",
        parent_run_id="run-parent",
        workspace_path="/workspace",
        capability_revision="cap-rev-1",
        allowed_capabilities=frozenset({"web:search", "web:fetch"}),
        evidence_sufficient=False,
        remaining_token_budget=2_000,
    )

    assert outcome.status in {"blocked", "source_gap"}
    assert outcome.reason_code == reason
