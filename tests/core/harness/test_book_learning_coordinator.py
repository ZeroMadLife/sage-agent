from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from sage_harness import (
    EvidenceBundle,
    EvidenceBundleItem,
    KnowledgeEvidence,
    KnowledgeRetrievalResult,
    SubagentProfile,
    SubagentRequest,
    SubagentResult,
    SubagentToolConfig,
)

from core.harness.book_learning_coordinator import (
    BookLearningCoordinator,
    BookLearningCoordinatorConfig,
    BookLearningCoordinatorRequest,
)
from core.harness.learning_intent import LearningIntentRoute


class FakeKnowledgePort:
    workspace_id = "knowledge-workspace"
    available = True

    def __init__(self, results: Sequence[KnowledgeRetrievalResult]) -> None:
        self.results = list(results)
        self.queries: list[str] = []

    async def search(
        self,
        query: str,
        *,
        workspace_id: str,
        token_budget: int,
        top_k: int = 8,
    ) -> KnowledgeRetrievalResult:
        assert workspace_id == self.workspace_id
        self.queries.append(query)
        return self.results.pop(0)


class FakeEvidenceBundlePort:
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


class FakeSubagentExecutor:
    def __init__(self, research_refs: Sequence[tuple[str, ...]]) -> None:
        self.research_refs = list(research_refs)
        self.requests: list[SubagentRequest] = []

    async def execute(self, request: SubagentRequest, progress=None) -> SubagentResult:  # type: ignore[no-untyped-def]
        self.requests.append(request)
        if request.subagent_type == "research":
            refs = self.research_refs.pop(0)
            return SubagentResult(
                child_run_id=request.child_run_id,
                status="succeeded",
                result=f"Research evidence: {' '.join(refs)}",
                result_ref=f"subagent://thread/{request.child_run_id}",
                evidence_refs=refs,
                token_usage=700,
                model_calls=1,
                tool_count=1,
            )
        refs = request.evidence_refs
        return SubagentResult(
            child_run_id=request.child_run_id,
            status="succeeded",
            result=f"两个来源的结论一致 [{refs[0]}] [{refs[-1]}]",
            result_ref=f"subagent://thread/{request.child_run_id}",
            evidence_refs=refs,
            token_usage=500,
            model_calls=1,
            tool_count=1,
        )

    async def cancel(self, child_run_id: str, reason: str = "parent_cancelled") -> None:
        return None


def _route(*, mode: str = "agentic_candidate", depth: str = "multi_hop") -> LearningIntentRoute:
    return LearningIntentRoute(
        intent_family="compare" if depth == "multi_hop" else "explain",
        knowledge_scope="book",
        depth=depth,
        learning_stage="understand",
        routing_confidence=0.9,
        recommended_mode=mode,
        provider_id="sage.test-router",
        provider_revision="1",
    )


def _retrieval(*refs: str) -> KnowledgeRetrievalResult:
    return KnowledgeRetrievalResult(
        query="question",
        workspace_id="knowledge-workspace",
        status="evidence_found" if refs else "no_evidence",
        token_budget=3_000,
        used_tokens=200 * len(refs),
        omitted_count=0,
        evidence=tuple(
            KnowledgeEvidence(
                citation_id=ref,
                content=f"evidence for {ref}",
                page_revision=f"page-{index}",
                source_revision=f"source-{index}",
                metadata={"source_relative_path": f"book-{index}.txt", "title": ref},
            )
            for index, ref in enumerate(refs, start=1)
        ),
    )


def _bundle(*refs: str) -> EvidenceBundle:
    return EvidenceBundle(
        status="evidence_found" if refs else "no_evidence",
        items=tuple(
            EvidenceBundleItem(
                evidence_ref=ref,
                kind="knowledge",
                content=f"bundle evidence {ref}",
                source_ref=f"source-{index}",
                source_revision=f"revision-{index}",
                token_count=50,
            )
            for index, ref in enumerate(refs, start=1)
        ),
        requested_refs=tuple(refs),
        token_budget=4_000,
        used_tokens=50 * len(refs),
    )


def _config() -> SubagentToolConfig:
    return SubagentToolConfig(
        allowed_types=frozenset({"research", "synthesize"}),
        profiles=(
            SubagentProfile(
                name="research",
                tool_scope=("knowledge_search",),
                token_budget=6_000,
                timeout_seconds=30,
                max_steps=6,
            ),
            SubagentProfile(
                name="synthesize",
                tool_scope=("read_evidence_bundle",),
                token_budget=4_000,
                timeout_seconds=30,
                max_steps=4,
            ),
        ),
    )


def _request(route: LearningIntentRoute) -> BookLearningCoordinatorRequest:
    return BookLearningCoordinatorRequest(
        thread_id="thread-1",
        run_id="run-1",
        workspace_id="workspace-1",
        workspace_path="/workspace",
        query="比较两本书对分工的解释",
        query_fingerprint="query-fingerprint",
        route=route,
        selected_sources=frozenset({"knowledge"}),
    )


def test_coordinator_rejects_more_than_two_research_children() -> None:
    with pytest.raises(ValueError, match="between 1 and 2"):
        BookLearningCoordinatorConfig(max_research_children=3)


@pytest.mark.asyncio
async def test_direct_evidence_is_preloaded_without_unnecessary_children() -> None:
    knowledge = FakeKnowledgePort([_retrieval("kcite_direct")])
    executor = FakeSubagentExecutor([])
    coordinator = BookLearningCoordinator(
        knowledge_port=knowledge,
        evidence_bundle_port=FakeEvidenceBundlePort(_bundle()),
        subagent_executor=executor,
        subagent_config=_config(),
    )

    outcome = await coordinator.run(_request(_route(mode="single_pass", depth="direct")))

    assert outcome.decision == "answer"
    assert outcome.final_answer == ""
    assert outcome.retrieval_rounds == 1
    assert outcome.child_run_ids == ()
    assert outcome.context["evidence"][0]["citation_id"] == "kcite_direct"
    assert executor.requests == []


@pytest.mark.asyncio
async def test_agentic_gap_runs_research_and_citation_guarded_synthesis() -> None:
    executor = FakeSubagentExecutor([("kcite_new_a",), ("kcite_new_b",)])
    coordinator = BookLearningCoordinator(
        knowledge_port=FakeKnowledgePort([_retrieval("kcite_initial")]),
        evidence_bundle_port=FakeEvidenceBundlePort(_bundle("kcite_new_a", "kcite_new_b")),
        subagent_executor=executor,
        subagent_config=_config(),
    )

    outcome = await coordinator.run(_request(_route()))

    assert outcome.decision == "answer"
    assert "kcite_new_a" in outcome.final_answer
    assert outcome.retrieval_rounds == 2
    assert len(outcome.child_run_ids) == 3
    assert [request.subagent_type for request in executor.requests] == [
        "research",
        "research",
        "synthesize",
    ]
    assert all("independent_evidence" in request.prompt for request in executor.requests[:2])
    assert outcome.stop_reason == "evidence_sufficient"


@pytest.mark.asyncio
async def test_second_round_without_new_citation_abstains() -> None:
    executor = FakeSubagentExecutor([("kcite_initial",), ("kcite_initial",)])
    coordinator = BookLearningCoordinator(
        knowledge_port=FakeKnowledgePort([_retrieval("kcite_initial")]),
        evidence_bundle_port=FakeEvidenceBundlePort(_bundle("kcite_initial")),
        subagent_executor=executor,
        subagent_config=_config(),
    )

    outcome = await coordinator.run(_request(_route()))

    assert outcome.decision == "abstain"
    assert outcome.stop_reason == "no_new_evidence"
    assert "没有获得新的可核验引用" in outcome.final_answer


@pytest.mark.asyncio
async def test_public_events_do_not_expose_query_or_citation_ids() -> None:
    coordinator = BookLearningCoordinator(
        knowledge_port=FakeKnowledgePort([_retrieval("kcite_private")]),
        evidence_bundle_port=FakeEvidenceBundlePort(_bundle()),
        subagent_executor=FakeSubagentExecutor([]),
        subagent_config=_config(),
    )

    outcome = await coordinator.run(_request(_route(mode="single_pass", depth="direct")))
    rendered = repr(outcome.public_events)

    assert "比较两本书" not in rendered
    assert "kcite_private" not in rendered
    assert "query-fingerprint" in rendered
    assert outcome.public_events[-1]["type"] == "agentic_rag_completed"


@pytest.mark.asyncio
async def test_research_children_are_started_with_a_bounded_parallel_window() -> None:
    class SlowExecutor(FakeSubagentExecutor):
        running = 0
        peak = 0

        async def execute(self, request: SubagentRequest, progress=None):  # type: ignore[no-untyped-def]
            type(self).running += 1
            type(self).peak = max(type(self).peak, type(self).running)
            try:
                await asyncio.sleep(0)
                return await super().execute(request, progress)
            finally:
                type(self).running -= 1

    executor = SlowExecutor([("kcite_a",), ("kcite_b",)])
    coordinator = BookLearningCoordinator(
        knowledge_port=FakeKnowledgePort([_retrieval("kcite_initial")]),
        evidence_bundle_port=FakeEvidenceBundlePort(_bundle("kcite_a", "kcite_b")),
        subagent_executor=executor,
        subagent_config=_config(),
    )

    outcome = await coordinator.run(_request(_route()))

    assert outcome.decision == "answer"
    assert executor.peak == 2


@pytest.mark.asyncio
async def test_retrieval_failure_abstains_instead_of_falling_back_to_uncited_answer() -> None:
    class FailingKnowledgePort(FakeKnowledgePort):
        async def search(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            del args, kwargs
            raise RuntimeError("index unavailable")

    coordinator = BookLearningCoordinator(
        knowledge_port=FailingKnowledgePort([]),
        evidence_bundle_port=FakeEvidenceBundlePort(_bundle()),
        subagent_executor=FakeSubagentExecutor([]),
        subagent_config=_config(),
    )

    outcome = await coordinator.run(_request(_route(mode="single_pass", depth="direct")))

    assert outcome.decision == "abstain"
    assert outcome.stop_reason == "retrieval_failed"
    assert "无法核验来源" in outcome.final_answer
