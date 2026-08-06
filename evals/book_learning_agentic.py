"""Deterministic stage metrics for bounded Agentic RAG outcomes.

This module evaluates recorded coordinator outcomes. It deliberately does not
pretend to measure semantic faithfulness from a single model score: citation
support and human/offline labels must be supplied as explicit fields.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgenticEvalCase:
    case_id: str
    expected_decision: str
    expected_agentic: bool
    expected_citations: tuple[str, ...]
    actual_decision: str
    actual_stop_reason: str
    actual_citations: tuple[str, ...]
    answer_citations: tuple[str, ...]
    child_count: int
    retrieval_rounds: int
    token_usage: int
    latency_ms: int
    faithfulness: bool | None = None
    answer_relevance: bool | None = None


def evaluate_agentic_outcomes(cases: Iterable[AgenticEvalCase]) -> dict[str, object]:
    """Return bounded Agentic RAG metrics without inferring missing labels."""

    evaluated = list(cases)
    if not evaluated:
        raise ValueError("agentic evaluation requires cases")
    for case in evaluated:
        if (
            case.child_count < 0
            or case.retrieval_rounds < 0
            or case.token_usage < 0
            or case.latency_ms < 0
        ):
            raise ValueError(f"invalid resource counters for {case.case_id}")

    coverage = [_coverage(case.expected_citations, case.actual_citations) for case in evaluated]
    citation_support = [_citation_support(case) for case in evaluated]
    false_acceptance = [
        case.expected_decision == "abstain" and case.actual_decision == "answer"
        for case in evaluated
    ]
    unnecessary_delegation = [
        not case.expected_agentic and case.child_count > 0 for case in evaluated
    ]
    faithfulness = [case.faithfulness for case in evaluated if case.faithfulness is not None]
    relevance = [case.answer_relevance for case in evaluated if case.answer_relevance is not None]
    return {
        "schema_version": 1,
        "stage": "agentic_rag",
        "protocol": {
            "semantic_faithfulness_requires_explicit_label": True,
            "llm_judge_only": False,
            "production_seam": "core.harness.book_learning_coordinator.BookLearningCoordinator",
        },
        "metrics": {
            "case_count": len(evaluated),
            "evidence_coverage": _mean(coverage),
            "citation_support": _mean(citation_support),
            "false_acceptance_rate": _rate(false_acceptance),
            "unnecessary_delegation_rate": _rate(unnecessary_delegation),
            "mean_token_usage": _mean([float(case.token_usage) for case in evaluated]),
            "mean_retrieval_rounds": _mean([float(case.retrieval_rounds) for case in evaluated]),
            "mean_child_count": _mean([float(case.child_count) for case in evaluated]),
            "p95_latency_ms": _percentile([case.latency_ms for case in evaluated], 0.95),
            "faithfulness_labeled_rate": _rate(faithfulness) if faithfulness else None,
            "answer_relevance_labeled_rate": _rate(relevance) if relevance else None,
        },
        "stop_reasons": _counts(case.actual_stop_reason for case in evaluated),
        "cases": [_case_payload(case) for case in evaluated],
    }


def _case_payload(case: AgenticEvalCase) -> dict[str, object]:
    return {
        "case_id": case.case_id,
        "expected_decision": case.expected_decision,
        "actual_decision": case.actual_decision,
        "actual_stop_reason": case.actual_stop_reason,
        "evidence_coverage": _coverage(case.expected_citations, case.actual_citations),
        "citation_support": _citation_support(case),
        "child_count": case.child_count,
        "retrieval_rounds": case.retrieval_rounds,
        "token_usage": case.token_usage,
        "latency_ms": case.latency_ms,
        "faithfulness": case.faithfulness,
        "answer_relevance": case.answer_relevance,
    }


def _coverage(expected: tuple[str, ...], actual: tuple[str, ...]) -> float:
    expected_set = set(expected)
    return len(expected_set.intersection(actual)) / len(expected_set) if expected_set else 1.0


def _citation_support(case: AgenticEvalCase) -> float:
    if case.actual_decision != "answer":
        return 1.0
    if not case.answer_citations:
        return 0.0
    return _coverage(case.answer_citations, case.actual_citations)


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _rate(values: Iterable[bool]) -> float:
    normalized = list(values)
    return round(sum(normalized) / len(normalized), 4) if normalized else 0.0


def _counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile + 0.9999) - 1))
    return ordered[index]


__all__ = ["AgenticEvalCase", "evaluate_agentic_outcomes"]
