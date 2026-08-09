"""Server-owned, bounded orchestration for book-learning turns."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from sage_harness import (
    EvidenceBundle,
    EvidenceBundlePort,
    KnowledgePort,
    SubagentExecutorPort,
    SubagentRequest,
    SubagentResult,
    SubagentToolConfig,
    derive_child_run_id,
)

from core.harness.learning_intent import LearningIntentRoute
from core.harness.retrieval_sufficiency import (
    RetrievalSufficiencyAssessment,
    evaluate_retrieval_sufficiency,
)

_CONFLICT_PATTERN = re.compile(r"(?:冲突|矛盾|不一致|contradict|disagree|conflict)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class BookLearningCoordinatorConfig:
    """Hard server limits for one coordinator invocation."""

    max_research_children: int = 2
    first_round_top_k: int = 8
    first_round_token_budget: int = 3_000
    retry_token_budget: int = 3_000
    research_token_budget: int = 6_000
    synthesis_token_budget: int = 4_000
    max_research_steps: int = 8
    max_synthesis_steps: int = 6
    evidence_token_budget: int = 4_000

    def __post_init__(self) -> None:
        if not 1 <= self.max_research_children <= 2:
            raise ValueError("max_research_children must be between 1 and 2")
        if not 1 <= self.first_round_top_k <= 20:
            raise ValueError("first_round_top_k must be between 1 and 20")
        for name in (
            "first_round_token_budget",
            "retry_token_budget",
            "research_token_budget",
            "synthesis_token_budget",
            "evidence_token_budget",
        ):
            value = int(getattr(self, name))
            if value < 256 or value > 20_000:
                raise ValueError(f"{name} must be between 256 and 20000")
        if self.max_research_steps < 1 or self.max_synthesis_steps < 1:
            raise ValueError("subagent steps must be positive")


@dataclass(frozen=True, slots=True)
class BookLearningCoordinatorRequest:
    """Immutable identity and route inputs supplied by the host runtime."""

    thread_id: str
    run_id: str
    workspace_id: str
    workspace_path: str
    query: str
    query_fingerprint: str
    route: LearningIntentRoute
    selected_sources: frozenset[str]

    def __post_init__(self) -> None:
        for name in (
            "thread_id",
            "run_id",
            "workspace_id",
            "workspace_path",
            "query",
            "query_fingerprint",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        if len(self.query) > 2_000:
            raise ValueError("query must not exceed 2000 characters")


@dataclass(frozen=True, slots=True)
class BookLearningCoordinatorOutcome:
    """Bounded result used both by the graph and the public timeline."""

    activated: bool
    decision: str
    stop_reason: str
    retrieval_rounds: int
    child_run_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    final_answer: str = ""
    context: Mapping[str, object] = field(default_factory=dict)
    public_events: tuple[Mapping[str, object], ...] = ()
    token_usage: int = 0
    latency_ms: int = 0


class BookLearningCoordinator:
    """Run the server-owned first-pass -> research -> synthesis state machine."""

    def __init__(
        self,
        *,
        knowledge_port: KnowledgePort | None,
        evidence_bundle_port: EvidenceBundlePort,
        subagent_executor: SubagentExecutorPort,
        subagent_config: SubagentToolConfig,
        config: BookLearningCoordinatorConfig | None = None,
    ) -> None:
        self.knowledge_port = knowledge_port
        self.evidence_bundle_port = evidence_bundle_port
        self.subagent_executor = subagent_executor
        self.subagent_config = subagent_config
        self.config = config or BookLearningCoordinatorConfig()

    async def run(
        self,
        request: BookLearningCoordinatorRequest,
    ) -> BookLearningCoordinatorOutcome:
        started_at = time.monotonic()
        route = request.route
        activated = bool(
            self.knowledge_port is not None
            and self.knowledge_port.available
            and "knowledge" in request.selected_sources
            and route.recommended_mode != "skip"
            and route.intent_family != "meta"
        )
        if not activated:
            return self._outcome(
                request,
                activated=False,
                decision="skip",
                stop_reason="route_not_eligible",
                started_at=started_at,
            )

        assert self.knowledge_port is not None
        try:
            first = await self.knowledge_port.search(
                request.query,
                workspace_id=self.knowledge_port.workspace_id,
                top_k=self.config.first_round_top_k,
                token_budget=self.config.first_round_token_budget,
            )
        except Exception:
            return self._abstain(
                request,
                retrieval_rounds=1,
                reason="retrieval_failed",
                previous_refs=(),
                public_events=[],
                token_usage=0,
                started_at=started_at,
            )
        first_refs = _citation_refs(first)
        first_sources = _source_refs(first)
        agentic_candidate = route.recommended_mode == "agentic_candidate"
        first_assessment = self._assess(
            request,
            round_index=1,
            citation_refs=first_refs,
            source_refs=first_sources,
            actual_hit_count=len(first.evidence),
            agentic_candidate=agentic_candidate,
            retry_available=not agentic_candidate and not first_refs,
        )
        public_events: list[Mapping[str, object]] = [
            self._assessment_event(request, first_assessment),
        ]

        if first_assessment.decision == "answer":
            return self._outcome(
                request,
                activated=True,
                decision="answer",
                stop_reason=first_assessment.stop_reason or "evidence_sufficient",
                retrieval_rounds=1,
                evidence_refs=first_refs,
                context=self._retrieval_context(first, first_assessment),
                public_events=public_events,
                token_usage=first.used_tokens,
                started_at=started_at,
            )

        if first_assessment.decision == "retry":
            retry_query = _retry_query(
                request.query,
                request.route,
                missing_aspects=first_assessment.missing_aspects,
            )
            try:
                second = await self.knowledge_port.search(
                    retry_query,
                    workspace_id=self.knowledge_port.workspace_id,
                    top_k=self.config.first_round_top_k,
                    token_budget=self.config.retry_token_budget,
                )
            except Exception:
                return self._abstain(
                    request,
                    retrieval_rounds=2,
                    reason="retrieval_failed",
                    previous_refs=first_refs,
                    public_events=public_events,
                    token_usage=first.used_tokens,
                    started_at=started_at,
                )
            second_refs = _citation_refs(second)
            second_sources = _source_refs(second)
            second_assessment = self._assess(
                request,
                round_index=2,
                citation_refs=second_refs,
                source_refs=second_sources,
                actual_hit_count=len(second.evidence),
                previous_citation_refs=first_refs,
            )
            public_events.append(self._assessment_event(request, second_assessment))
            if second_assessment.decision == "answer":
                return self._outcome(
                    request,
                    activated=True,
                    decision="answer",
                    stop_reason=second_assessment.stop_reason or "evidence_sufficient",
                    retrieval_rounds=2,
                    evidence_refs=second_refs,
                    context=self._retrieval_context(second, second_assessment),
                    public_events=public_events,
                    token_usage=first.used_tokens + second.used_tokens,
                    started_at=started_at,
                )
            return self._abstain(
                request,
                retrieval_rounds=2,
                reason=second_assessment.stop_reason or "insufficient_evidence",
                previous_refs=first_refs,
                public_events=public_events,
                token_usage=first.used_tokens + second.used_tokens,
                started_at=started_at,
            )

        if first_assessment.decision != "delegate_research":
            return self._abstain(
                request,
                retrieval_rounds=1,
                reason=first_assessment.stop_reason or "insufficient_evidence",
                previous_refs=first_refs,
                public_events=public_events,
                token_usage=first.used_tokens,
                started_at=started_at,
            )

        return await self._run_agentic(
            request,
            first_assessment=first_assessment,
            initial_refs=first_refs,
            public_events=public_events,
            initial_token_usage=first.used_tokens,
            started_at=started_at,
        )

    async def _run_agentic(
        self,
        request: BookLearningCoordinatorRequest,
        *,
        first_assessment: RetrievalSufficiencyAssessment,
        initial_refs: tuple[str, ...],
        public_events: list[Mapping[str, object]],
        initial_token_usage: int,
        started_at: float,
    ) -> BookLearningCoordinatorOutcome:
        research_profile = self.subagent_config.resolve("research")
        synthesis_profile = self.subagent_config.resolve("synthesize")
        if research_profile is None or synthesis_profile is None:
            return self._abstain(
                request,
                retrieval_rounds=1,
                reason="agent_profiles_unavailable",
                previous_refs=initial_refs,
                public_events=public_events,
                token_usage=initial_token_usage,
                started_at=started_at,
            )

        child_ids: list[str] = []
        research_prompts = _research_prompts(
            request.query,
            request.route,
            missing_aspects=first_assessment.missing_aspects,
        )
        research_requests: list[SubagentRequest] = []
        for ordinal, prompt in enumerate(research_prompts[: self.config.max_research_children]):
            child_id = derive_child_run_id(
                request.thread_id,
                request.run_id,
                f"book-learning-research-{ordinal}",
            )
            child_ids.append(child_id)
            public_events.append(
                self._child_event(
                    request,
                    "subagent_started",
                    child_id,
                    "research",
                    ordinal + 1,
                )
            )
            research_requests.append(
                SubagentRequest(
                    parent_thread_id=request.thread_id,
                    parent_run_id=request.run_id,
                    child_run_id=child_id,
                    description=f"book-learning research {ordinal + 1}",
                    prompt=prompt,
                    subagent_type="research",
                    workspace_id=request.workspace_id,
                    workspace_path=request.workspace_path,
                    tool_scope=research_profile.tool_scope,
                    token_budget=min(
                        research_profile.token_budget,
                        self.config.research_token_budget,
                    ),
                    timeout_seconds=research_profile.timeout_seconds,
                    max_steps=min(research_profile.max_steps, self.config.max_research_steps),
                )
            )

        async def execute_research(child_request: SubagentRequest) -> SubagentResult:
            try:
                return await self.subagent_executor.execute(child_request)
            except Exception:
                return SubagentResult(
                    child_run_id=child_request.child_run_id,
                    status="failed",
                    error_code="research_execution_failed",
                )

        results = await asyncio.gather(
            *(execute_research(item) for item in research_requests),
        )
        successful_research: list[tuple[str, SubagentResult]] = []
        for child_request, result in zip(research_requests, results, strict=True):
            public_events.append(self._child_terminal_event(request, result, "research"))
            if result.status == "succeeded" and result.evidence_refs:
                successful_research.append((child_request.child_run_id, result))

        research_refs = tuple(
            dict.fromkeys(
                ref
                for _, result in successful_research
                for ref in result.evidence_refs
                if ref.strip()
            )
        )
        research_ids = tuple(child_id for child_id, _ in successful_research)
        if not research_refs or not research_ids:
            return self._abstain(
                request,
                retrieval_rounds=2,
                reason="research_no_evidence",
                previous_refs=initial_refs,
                child_run_ids=tuple(child_ids),
                public_events=public_events,
                token_usage=initial_token_usage,
                started_at=started_at,
            )

        try:
            bundle = await self.evidence_bundle_port.read(
                request.thread_id,
                request.run_id,
                child_run_ids=research_ids,
                evidence_refs=research_refs,
                token_budget=self.config.evidence_token_budget,
            )
        except Exception:
            return self._abstain(
                request,
                retrieval_rounds=2,
                reason="evidence_bundle_failed",
                previous_refs=initial_refs,
                child_run_ids=tuple(child_ids),
                public_events=public_events,
                token_usage=initial_token_usage,
                started_at=started_at,
            )
        if bundle.status != "evidence_found" or not bundle.items:
            return self._abstain(
                request,
                retrieval_rounds=2,
                reason="evidence_bundle_empty",
                previous_refs=initial_refs,
                child_run_ids=tuple(child_ids),
                public_events=public_events,
                token_usage=initial_token_usage,
                started_at=started_at,
            )

        synthesis_id = derive_child_run_id(
            request.thread_id,
            request.run_id,
            "book-learning-synthesize",
        )
        child_ids.append(synthesis_id)
        public_events.append(
            self._child_event(
                request,
                "subagent_started",
                synthesis_id,
                "synthesize",
                len(child_ids),
            )
        )
        synthesis_request = SubagentRequest(
            parent_thread_id=request.thread_id,
            parent_run_id=request.run_id,
            child_run_id=synthesis_id,
            description="book-learning evidence synthesis",
            prompt=(
                f"Synthesize an answer to: {request.query}\n"
                "Use only the authorized EvidenceBundle. Preserve at least one citation ID "
                "for every material claim and explicitly state unresolved conflicts."
            ),
            subagent_type="synthesize",
            workspace_id=request.workspace_id,
            workspace_path=request.workspace_path,
            tool_scope=synthesis_profile.tool_scope,
            token_budget=min(synthesis_profile.token_budget, self.config.synthesis_token_budget),
            timeout_seconds=synthesis_profile.timeout_seconds,
            max_steps=min(synthesis_profile.max_steps, self.config.max_synthesis_steps),
            evidence_refs=research_refs,
            evidence_child_run_ids=research_ids,
        )
        try:
            synthesis = await self.subagent_executor.execute(synthesis_request)
        except Exception:
            synthesis = SubagentResult(
                child_run_id=synthesis_id,
                status="failed",
                error_code="synthesis_execution_failed",
            )
        public_events.append(self._child_terminal_event(request, synthesis, "synthesize"))
        bundle_refs = tuple(item.evidence_ref for item in bundle.items)
        cited_refs = tuple(ref for ref in bundle_refs if ref in synthesis.result)
        conflict_count = sum(
            1 for _, result in successful_research if _CONFLICT_PATTERN.search(result.result)
        )
        final_assessment = self._assess(
            request,
            round_index=2,
            citation_refs=bundle_refs,
            source_refs=tuple(item.source_ref for item in bundle.items),
            actual_hit_count=len(bundle.items),
            conflict_count=conflict_count,
            previous_citation_refs=initial_refs,
        )
        public_events.append(self._assessment_event(request, final_assessment))
        total_tokens = (
            initial_token_usage
            + sum(result.token_usage for _, result in successful_research)
            + synthesis.token_usage
        )
        if synthesis.status != "succeeded" or not cited_refs:
            reason = "synthesis_uncited" if synthesis.status == "succeeded" else "synthesis_failed"
            return self._abstain(
                request,
                retrieval_rounds=2,
                reason=reason,
                previous_refs=initial_refs,
                child_run_ids=tuple(child_ids),
                public_events=public_events,
                token_usage=total_tokens,
                started_at=started_at,
            )
        if final_assessment.decision != "answer":
            return self._abstain(
                request,
                retrieval_rounds=2,
                reason=final_assessment.stop_reason or "insufficient_evidence",
                previous_refs=initial_refs,
                child_run_ids=tuple(child_ids),
                public_events=public_events,
                token_usage=total_tokens,
                started_at=started_at,
            )

        return self._outcome(
            request,
            activated=True,
            decision="answer",
            stop_reason=final_assessment.stop_reason or "evidence_sufficient",
            retrieval_rounds=2,
            child_run_ids=tuple(child_ids),
            evidence_refs=bundle_refs,
            final_answer=synthesis.result.strip()[:8_000],
            context=self._bundle_context(bundle, final_assessment),
            public_events=public_events,
            token_usage=total_tokens,
            started_at=started_at,
        )

    def _assess(
        self,
        request: BookLearningCoordinatorRequest,
        *,
        round_index: int,
        citation_refs: Sequence[str],
        source_refs: Sequence[str],
        actual_hit_count: int,
        previous_citation_refs: Sequence[str] = (),
        conflict_count: int = 0,
        agentic_candidate: bool = False,
        retry_available: bool = False,
    ) -> RetrievalSufficiencyAssessment:
        required = _required_aspects(request.route)
        covered = _covered_aspects(required, source_refs)
        return evaluate_retrieval_sufficiency(
            query_fingerprint=request.query_fingerprint,
            round_index=round_index,
            required_aspects=required,
            covered_aspects=covered,
            citation_refs=citation_refs,
            source_refs=source_refs,
            conflict_count=conflict_count,
            actual_hit_count=actual_hit_count,
            minimum_source_count=2 if len(required) > 1 else 1,
            retry_available=retry_available,
            agentic_candidate=agentic_candidate,
            previous_citation_refs=previous_citation_refs,
        )

    def _retrieval_context(
        self,
        result: object,
        assessment: RetrievalSufficiencyAssessment,
    ) -> dict[str, object]:
        evidence = getattr(result, "evidence", ())
        return {
            "version": 1,
            "decision": assessment.decision,
            "round_index": assessment.round_index,
            "required_aspects": list(assessment.required_aspects),
            "covered_aspects": list(assessment.covered_aspects),
            "missing_aspects": list(assessment.missing_aspects),
            "evidence": [_knowledge_evidence_context(item) for item in evidence[:8]],
        }

    def _bundle_context(
        self,
        bundle: EvidenceBundle,
        assessment: RetrievalSufficiencyAssessment,
    ) -> dict[str, object]:
        return {
            "version": 1,
            "decision": assessment.decision,
            "round_index": assessment.round_index,
            "required_aspects": list(assessment.required_aspects),
            "covered_aspects": list(assessment.covered_aspects),
            "missing_aspects": list(assessment.missing_aspects),
            "evidence": [
                {
                    "citation_id": item.evidence_ref[:160],
                    "kind": item.kind,
                    "title": item.title[:300],
                    "source_ref": item.source_ref[:200],
                    "source_revision": item.source_revision[:160],
                    "content": item.content[:4_000],
                }
                for item in bundle.items[:8]
            ],
        }

    def _assessment_event(
        self,
        request: BookLearningCoordinatorRequest,
        assessment: RetrievalSufficiencyAssessment,
    ) -> Mapping[str, object]:
        return {
            **assessment.to_public_payload(run_id=request.run_id),
            "type": "retrieval_sufficiency_assessed",
        }

    def _child_event(
        self,
        request: BookLearningCoordinatorRequest,
        event_type: str,
        child_id: str,
        child_type: str,
        ordinal: int,
    ) -> Mapping[str, object]:
        return {
            "type": event_type,
            "run_id": request.run_id,
            "parent_run_id": request.run_id,
            "child_run_id": child_id,
            "subagent_type": child_type,
            "description": f"book-learning {child_type} {ordinal}",
        }

    def _child_terminal_event(
        self,
        request: BookLearningCoordinatorRequest,
        result: SubagentResult,
        child_type: str,
    ) -> Mapping[str, object]:
        return {
            "type": "subagent_terminal",
            "run_id": request.run_id,
            "parent_run_id": request.run_id,
            "child_run_id": result.child_run_id,
            "subagent_type": child_type,
            "status": result.status,
            "result_ref": result.result_ref,
            "error_code": result.error_code,
            "evidence_count": len(result.evidence_refs),
            "token_usage": result.token_usage,
            "model_calls": result.model_calls,
            "tool_count": result.tool_count,
        }

    def _abstain(
        self,
        request: BookLearningCoordinatorRequest,
        *,
        retrieval_rounds: int,
        reason: str,
        previous_refs: Sequence[str],
        public_events: list[Mapping[str, object]],
        token_usage: int,
        started_at: float,
        child_run_ids: tuple[str, ...] = (),
    ) -> BookLearningCoordinatorOutcome:
        return self._outcome(
            request,
            activated=True,
            decision="abstain",
            stop_reason=reason,
            retrieval_rounds=retrieval_rounds,
            child_run_ids=child_run_ids,
            evidence_refs=tuple(previous_refs),
            final_answer=_abstention_message(reason),
            public_events=public_events,
            token_usage=token_usage,
            started_at=started_at,
        )

    def _outcome(
        self,
        request: BookLearningCoordinatorRequest,
        *,
        activated: bool,
        decision: str,
        stop_reason: str,
        retrieval_rounds: int = 0,
        child_run_ids: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        final_answer: str = "",
        context: Mapping[str, object] | None = None,
        public_events: Sequence[Mapping[str, object]] = (),
        token_usage: int = 0,
        started_at: float,
    ) -> BookLearningCoordinatorOutcome:
        events = [*public_events]
        events.append(
            {
                "type": "agentic_rag_completed",
                "run_id": request.run_id,
                "decision": decision,
                "retrieval_rounds": retrieval_rounds,
                "child_count": len(child_run_ids),
                "evidence_count": len(evidence_refs),
                "token_usage": token_usage,
                "stop_reason": stop_reason,
            }
        )
        return BookLearningCoordinatorOutcome(
            activated=activated,
            decision=decision,
            stop_reason=stop_reason,
            retrieval_rounds=retrieval_rounds,
            child_run_ids=child_run_ids,
            evidence_refs=evidence_refs,
            final_answer=final_answer,
            context=dict(context or {}),
            public_events=tuple(events),
            token_usage=token_usage,
            latency_ms=int((time.monotonic() - started_at) * 1000),
        )


def _required_aspects(route: LearningIntentRoute) -> tuple[str, ...]:
    if route.depth == "multi_hop" or route.intent_family in {"compare", "research"}:
        return ("primary_evidence", "independent_evidence")
    return ("primary_evidence",)


def _covered_aspects(required: Sequence[str], source_refs: Sequence[str]) -> tuple[str, ...]:
    unique_sources = tuple(dict.fromkeys(ref for ref in source_refs if ref.strip()))
    if not unique_sources:
        return ()
    if len(required) == 1:
        return (required[0],)
    covered = [required[0]]
    if len(unique_sources) >= 2:
        covered.append(required[1])
    return tuple(covered)


def _citation_refs(result: object) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(item.citation_id).strip()
            for item in getattr(result, "evidence", ())
            if str(getattr(item, "citation_id", "")).strip()
        )
    )


def _source_refs(result: object) -> tuple[str, ...]:
    refs: list[str] = []
    for item in getattr(result, "evidence", ()):
        metadata = getattr(item, "metadata", {})
        if isinstance(metadata, Mapping):
            source = str(
                metadata.get("source_revision")
                or metadata.get("source_relative_path")
                or getattr(item, "source_revision", "")
            ).strip()
        else:
            source = str(getattr(item, "source_revision", "")).strip()
        if source:
            refs.append(source)
    return tuple(dict.fromkeys(refs))


def _knowledge_evidence_context(item: object) -> dict[str, object]:
    metadata = getattr(item, "metadata", {})
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return {
        "citation_id": str(getattr(item, "citation_id", ""))[:160],
        "title": str(metadata.get("title", ""))[:300],
        "source_revision": str(getattr(item, "source_revision", ""))[:160],
        "page_revision": str(getattr(item, "page_revision", ""))[:160],
        "source_path": str(metadata.get("source_relative_path", ""))[:500],
        "content": str(getattr(item, "content", ""))[:4_000],
    }


def _retry_query(
    query: str,
    route: LearningIntentRoute,
    *,
    missing_aspects: Sequence[str] = (),
) -> str:
    normalized = re.sub(
        r"(?:请|帮我|能否|可以|解释一下|简单说说|告诉我)",
        " ",
        query,
        flags=re.IGNORECASE,
    )
    topics = " ".join(route.candidate_topics[:3])
    gap_hint = " ".join(str(item).strip() for item in missing_aspects if str(item).strip())
    candidate = " ".join(normalized.split())
    if topics and topics not in candidate:
        candidate = f"{candidate} {topics}".strip()
    if gap_hint:
        candidate = f"{candidate} {gap_hint}".strip()
    return candidate[:2_000] or query


def _research_prompts(
    query: str,
    route: LearningIntentRoute,
    *,
    missing_aspects: Sequence[str] = (),
) -> tuple[str, ...]:
    topic_hint = "、".join(route.candidate_topics[:3])
    suffix = f"关注主题：{topic_hint}" if topic_hint else ""
    gap_hint = "、".join(str(item).strip() for item in missing_aspects if str(item).strip())
    gap = f"当前缺口：{gap_hint}。" if gap_hint else ""
    return (
        f"针对问题“{query}”，只寻找第一组直接原文证据，解释核心定义、机制和因果链。{gap}{suffix}",
        f"针对问题“{query}”，寻找独立来源或另一章节的补充/对照证据，指出与第一组证据的共识或冲突。{gap}{suffix}",
    )


def _abstention_message(reason: str) -> str:
    if reason in {"retrieval_failed", "evidence_bundle_failed"}:
        return "当前书籍检索暂时不可用，我无法核验来源，因此先不生成结论。"
    return "当前书籍证据不足，且在有界检索内没有获得新的可核验引用。" "我先不把不确定内容当作结论。"


__all__ = [
    "BookLearningCoordinator",
    "BookLearningCoordinatorConfig",
    "BookLearningCoordinatorOutcome",
    "BookLearningCoordinatorRequest",
]
