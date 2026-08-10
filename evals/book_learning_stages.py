"""Deterministic metrics for the book-learning retrieval and answer stages.

The evaluator consumes recorded receipts and explicit labels. It never asks an
LLM to reveal chain-of-thought and it does not treat a judge score as fact.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecoveryEvalCase:
    case_id: str
    answerable: bool
    gold_evidence: tuple[str, ...]
    required_claims: tuple[str, ...]
    first_round_evidence: tuple[str, ...]
    final_evidence: tuple[str, ...]
    first_round_claims: tuple[str, ...]
    final_claims: tuple[str, ...]
    final_decision: str
    first_round_latency_ms: int
    recovery_latency_ms: int
    rewrite_preserved_intent: bool | None = None


@dataclass(frozen=True, slots=True)
class GenerationEvalCase:
    case_id: str
    answerable: bool
    final_decision: str
    required_claims: tuple[str, ...]
    present_claims: tuple[str, ...]
    unsupported_claims: tuple[str, ...]
    answer_citations: tuple[str, ...]
    supported_citations: tuple[str, ...]
    generated_claims: tuple[str, ...] = ()
    supported_generated_claims: tuple[str, ...] = ()
    gold_claim_ids: tuple[str, ...] = ()
    covered_gold_claim_ids: tuple[str, ...] = ()
    contradicted_gold_claim_ids: tuple[str, ...] = ()
    unsupported_gold_claim_ids: tuple[str, ...] = ()
    context_precision: float | None = None
    context_recall: float | None = None
    faithfulness: float | None = None
    answer_relevance: float | None = None
    evaluation_status: str = "completed"


def evaluate_recovery(cases: Iterable[RecoveryEvalCase]) -> dict[str, object]:
    """Measure whether one bounded recovery round adds the missing evidence."""

    evaluated = list(cases)
    if not evaluated:
        raise ValueError("recovery evaluation requires cases")
    for case in evaluated:
        if case.first_round_latency_ms < 0 or case.recovery_latency_ms < 0:
            raise ValueError(f"invalid latency for {case.case_id}")
        if case.final_decision not in {"answer", "abstain"}:
            raise ValueError(f"invalid final decision for {case.case_id}")

    answerable = [case for case in evaluated if case.answerable]
    unanswerable = [case for case in evaluated if not case.answerable]
    first_recall = [_coverage(case.gold_evidence, case.first_round_evidence) for case in answerable]
    final_recall = [_coverage(case.gold_evidence, case.final_evidence) for case in answerable]
    first_claim_coverage = [
        _coverage(case.required_claims, case.first_round_claims) for case in answerable
    ]
    final_claim_coverage = [
        _coverage(case.required_claims, case.final_claims) for case in answerable
    ]
    recoverable = [
        (first, final)
        for first, final in zip(first_recall, final_recall, strict=True)
        if first < 1.0
    ]
    rewrite_labels = [
        case.rewrite_preserved_intent
        for case in evaluated
        if case.rewrite_preserved_intent is not None
    ]
    return {
        "schema_version": 1,
        "stage": "bounded_recovery",
        "protocol": {
            "max_recovery_rounds": 1,
            "query_rewrite_uses_missing_claim_receipts": True,
            "chain_of_thought_required": False,
            "rewrite_quality_requires_explicit_label": True,
            "claim_coverage_method": "explicit_receipt_or_gold_evidence_proxy",
        },
        "metrics": {
            "case_count": len(evaluated),
            "answerable_case_count": len(answerable),
            "unanswerable_case_count": len(unanswerable),
            "first_pass_evidence_recall": _mean(first_recall),
            "final_evidence_recall": _mean(final_recall),
            "incremental_evidence_recall_gain": _mean(
                [final - first for first, final in zip(first_recall, final_recall, strict=True)]
            ),
            "first_pass_claim_coverage": _mean(first_claim_coverage),
            "final_claim_coverage": _mean(final_claim_coverage),
            "recovery_success_rate": _rate(final > first for first, final in recoverable),
            "no_new_evidence_rate": _rate(
                not (set(case.final_evidence) - set(case.first_round_evidence))
                for case in evaluated
            ),
            "query_drift_rate": (
                _rate(not bool(value) for value in rewrite_labels) if rewrite_labels else None
            ),
            "false_acceptance_rate": _rate(
                case.final_decision == "answer" for case in unanswerable
            ),
            "correct_abstention_rate": _rate(
                case.final_decision == "abstain" for case in unanswerable
            ),
            "first_round_p95_latency_ms": _percentile(
                [case.first_round_latency_ms for case in evaluated], 0.95
            ),
            "recovery_p95_latency_ms": _percentile(
                [case.recovery_latency_ms for case in evaluated], 0.95
            ),
        },
    }


def evaluate_generation(cases: Iterable[GenerationEvalCase]) -> dict[str, object]:
    """Measure user-visible answer quality with citation and claim labels."""

    evaluated = list(cases)
    if not evaluated:
        raise ValueError("generation evaluation requires cases")
    if any(case.evaluation_status not in {"completed", "provider_error"} for case in evaluated):
        raise ValueError("invalid generation evaluation status")
    completed = [case for case in evaluated if case.evaluation_status == "completed"]
    failures = [case for case in evaluated if case.evaluation_status == "provider_error"]
    faithfulness = [case.faithfulness for case in completed if case.faithfulness is not None]
    relevance = [case.answer_relevance for case in completed if case.answer_relevance is not None]
    context_precision = [
        case.context_precision for case in completed if case.context_precision is not None
    ]
    context_recall = [case.context_recall for case in completed if case.context_recall is not None]
    generated_claim_count = sum(len(case.generated_claims) for case in completed)
    supported_generated_claims = sum(
        len(case.supported_generated_claims) for case in completed if case.generated_claims
    )
    gold_claim_cases = [case for case in completed if case.answerable and case.gold_claim_ids]
    answer_claim_coverage = _mean(
        _coverage(case.gold_claim_ids, case.covered_gold_claim_ids) for case in gold_claim_cases
    )
    answer_correctness = _rate(
        case.final_decision == "answer"
        and set(case.gold_claim_ids).issubset(case.covered_gold_claim_ids)
        and not set(case.contradicted_gold_claim_ids)
        for case in gold_claim_cases
    )
    legacy_claim_coverage = _mean(
        [
            _coverage(case.required_claims, case.present_claims)
            for case in completed
            if case.answerable
        ]
    )
    return {
        "schema_version": 1,
        "stage": "generation",
        "protocol": {
            "ragas_compatible_metrics": [
                "context_precision",
                "context_recall",
                "faithfulness",
                "answer_relevancy",
            ],
            "ragas_is_auxiliary": True,
            "citation_and_claim_labels_are_activation_gates": True,
            "answer_correctness_uses_atomic_gold_claims": True,
            "provider_failures_excluded_from_quality_denominators": True,
        },
        "metrics": {
            "case_count": len(evaluated),
            "evaluated_case_count": len(completed),
            "provider_failure_count": len(failures),
            "provider_failure_rate": round(len(failures) / len(evaluated), 4),
            "claim_coverage": (
                answer_claim_coverage if gold_claim_cases else legacy_claim_coverage
            ),
            "answer_claim_coverage": answer_claim_coverage if gold_claim_cases else None,
            "answer_correctness": answer_correctness if gold_claim_cases else None,
            "gold_claim_case_count": len(gold_claim_cases),
            "covered_gold_claim_count": sum(
                len(set(case.covered_gold_claim_ids).intersection(case.gold_claim_ids))
                for case in gold_claim_cases
            ),
            "contradicted_gold_claim_count": sum(
                len(set(case.contradicted_gold_claim_ids).intersection(case.gold_claim_ids))
                for case in gold_claim_cases
            ),
            "unsupported_gold_claim_count": sum(
                len(set(case.unsupported_gold_claim_ids).intersection(case.gold_claim_ids))
                for case in gold_claim_cases
            ),
            "unsupported_claim_rate": _mean(
                [
                    (
                        len(set(case.unsupported_claims)) / max(1, len(set(case.generated_claims)))
                        if case.generated_claims
                        else len(set(case.unsupported_claims))
                        / max(1, len(set(case.present_claims)))
                    )
                    for case in completed
                ]
            ),
            "citation_correctness": _mean(
                [
                    _coverage(case.answer_citations, case.supported_citations)
                    if case.answer_citations
                    else (1.0 if case.final_decision == "abstain" else 0.0)
                    for case in completed
                ]
            ),
            "false_acceptance_rate": _rate(
                case.final_decision == "answer" for case in completed if not case.answerable
            ),
            "correct_abstention_rate": _rate(
                case.final_decision == "abstain" for case in completed if not case.answerable
            ),
            "answerable_answer_rate": _rate(
                case.final_decision == "answer" for case in completed if case.answerable
            ),
            "generated_claim_count": generated_claim_count,
            "supported_generated_claim_count": supported_generated_claims,
            "context_precision": _mean(context_precision) if context_precision else None,
            "context_recall": _mean(context_recall) if context_recall else None,
            "faithfulness": _mean(faithfulness) if faithfulness else None,
            "answer_relevance": _mean(relevance) if relevance else None,
            "faithfulness_labeled_count": len(faithfulness),
            "answer_relevance_labeled_count": len(relevance),
        },
    }


def _coverage(expected: tuple[str, ...], actual: tuple[str, ...]) -> float:
    expected_set = set(expected)
    return len(expected_set.intersection(actual)) / len(expected_set) if expected_set else 1.0


def _mean(values: Iterable[float]) -> float:
    normalized = list(values)
    return round(sum(normalized) / len(normalized), 4) if normalized else 0.0


def _rate(values: Iterable[bool]) -> float:
    normalized = list(values)
    return round(sum(normalized) / len(normalized), 4) if normalized else 0.0


def _percentile(values: list[int], percentile: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile + 0.9999) - 1))
    return ordered[index]


__all__ = [
    "GenerationEvalCase",
    "RecoveryEvalCase",
    "evaluate_generation",
    "evaluate_recovery",
]
