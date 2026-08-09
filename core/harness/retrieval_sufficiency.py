"""Server-owned evidence sufficiency policy for bounded Agentic RAG."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

SufficiencyDecision = Literal["answer", "retry", "delegate_research", "abstain"]


@dataclass(frozen=True, slots=True)
class RetrievalSufficiencyAssessment:
    """One content-free, auditable decision about a retrieval round."""

    version: int
    query_fingerprint: str
    round_index: int
    sufficient: bool
    decision: SufficiencyDecision
    required_aspects: tuple[str, ...]
    covered_aspects: tuple[str, ...]
    missing_aspects: tuple[str, ...]
    covered_citation_refs: tuple[str, ...]
    covered_sources: tuple[str, ...]
    conflict_count: int
    actual_hit_count: int
    route_reason: str
    stop_reason: str | None = None
    model_confidence: float | None = None

    def to_payload(self) -> dict[str, object]:
        """Return the durable internal receipt, including bounded aspect labels."""
        return {
            "type": "retrieval_sufficiency_assessed",
            "version": self.version,
            "query_fingerprint": self.query_fingerprint,
            "round_index": self.round_index,
            "sufficient": self.sufficient,
            "decision": self.decision,
            "required_aspects": list(self.required_aspects),
            "covered_aspects": list(self.covered_aspects),
            "missing_aspects": list(self.missing_aspects),
            "covered_citation_count": len(self.covered_citation_refs),
            "covered_source_count": len(self.covered_sources),
            "conflict_count": self.conflict_count,
            "actual_hit_count": self.actual_hit_count,
            "route_reason": self.route_reason,
            "stop_reason": self.stop_reason,
            "model_confidence": self.model_confidence,
        }

    def to_public_payload(self, *, run_id: str) -> dict[str, object]:
        """Project a content-free timeline receipt without query-derived labels."""
        return {
            "type": "retrieval_sufficiency_assessed",
            "version": self.version,
            "run_id": run_id,
            "query_fingerprint": self.query_fingerprint,
            "round_index": self.round_index,
            "sufficient": self.sufficient,
            "decision": self.decision,
            "covered_citation_count": len(self.covered_citation_refs),
            "covered_source_count": len(self.covered_sources),
            "actual_hit_count": self.actual_hit_count,
            "conflict_count": self.conflict_count,
            "route_reason": self.route_reason,
            "stop_reason": self.stop_reason,
        }


def evaluate_retrieval_sufficiency(
    *,
    query_fingerprint: str,
    round_index: int,
    required_aspects: Sequence[str],
    covered_aspects: Sequence[str],
    citation_refs: Iterable[str],
    source_refs: Iterable[str],
    conflict_count: int = 0,
    actual_hit_count: int | None = None,
    minimum_source_count: int = 1,
    retry_available: bool = False,
    agentic_candidate: bool = False,
    budget_available: bool = True,
    previous_citation_refs: Iterable[str] = (),
    model_confidence: float | None = None,
) -> RetrievalSufficiencyAssessment:
    """Apply the server policy to one retrieval result.

    ``model_confidence`` is retained for evaluation only. It never makes a
    citation-free or conflict-bearing result answerable.
    """
    if not query_fingerprint or len(query_fingerprint) > 128:
        raise ValueError("query fingerprint is invalid")
    if round_index not in {1, 2}:
        raise ValueError("retrieval sufficiency supports rounds one and two only")
    if minimum_source_count < 1:
        raise ValueError("minimum source count must be positive")
    if conflict_count < 0:
        raise ValueError("conflict count must be non-negative")
    required = _labels(required_aspects)
    if not required:
        raise ValueError("at least one required aspect is needed")
    covered = tuple(label for label in _labels(covered_aspects) if label in required)
    missing = tuple(label for label in required if label not in covered)
    citations = _refs(citation_refs)
    sources = _refs(source_refs)
    previous = set(_refs(previous_citation_refs))
    new_citations = citations.difference(previous)
    hit_count = max(0, actual_hit_count if actual_hit_count is not None else len(citations))
    confidence = None if model_confidence is None else min(1.0, max(0.0, model_confidence))

    evidence_complete = (
        not missing
        and bool(citations)
        and hit_count > 0
        and len(sources) >= minimum_source_count
        and conflict_count == 0
    )
    if round_index == 2 and not new_citations:
        decision: SufficiencyDecision = "abstain"
        route_reason = "no_new_evidence"
        stop_reason = "no_new_evidence"
        sufficient = False
    elif evidence_complete:
        decision = "answer"
        route_reason = "evidence_sufficient"
        stop_reason = "evidence_sufficient"
        sufficient = True
    elif round_index == 2:
        decision = "abstain"
        route_reason = "bounded_round_limit"
        stop_reason = "conflict_unresolved" if conflict_count else "round_limit_reached"
        sufficient = False
    elif conflict_count and not agentic_candidate:
        decision = "abstain"
        route_reason = "conflict_requires_review"
        stop_reason = "conflict_unresolved"
        sufficient = False
    elif not budget_available:
        decision = "abstain"
        route_reason = "budget_exhausted"
        stop_reason = "budget_exhausted"
        sufficient = False
    elif retry_available:
        decision = "retry"
        route_reason = "retrieval_gap_may_be_recoverable"
        stop_reason = None
        sufficient = False
    elif agentic_candidate:
        decision = "delegate_research"
        route_reason = "cross_source_or_multi_hop_gap"
        stop_reason = None
        sufficient = False
    else:
        decision = "abstain"
        route_reason = "insufficient_authorized_evidence"
        stop_reason = "insufficient_evidence"
        sufficient = False

    return RetrievalSufficiencyAssessment(
        version=1,
        query_fingerprint=query_fingerprint,
        round_index=round_index,
        sufficient=sufficient,
        decision=decision,
        required_aspects=required,
        covered_aspects=covered,
        missing_aspects=missing,
        covered_citation_refs=tuple(sorted(citations)),
        covered_sources=tuple(sorted(sources)),
        conflict_count=conflict_count,
        actual_hit_count=hit_count,
        route_reason=route_reason,
        stop_reason=stop_reason,
        model_confidence=confidence,
    )


def _labels(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip()[:120] for value in values if str(value).strip()))


def _refs(values: Iterable[str]) -> set[str]:
    return {str(value).strip()[:160] for value in values if str(value).strip()}


__all__ = [
    "RetrievalSufficiencyAssessment",
    "SufficiencyDecision",
    "evaluate_retrieval_sufficiency",
]
