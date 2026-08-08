"""Product scorecard for the long-book RAG evaluation loop.

The scorecard keeps user outcomes separate from diagnostic metrics. It only
recommends a component to inspect; it never mutates an online retrieval policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ScorecardThresholds:
    """Frozen v1 decision thresholds for offline diagnosis."""

    first_pass_claim_coverage_min: float = 0.8
    answer_correctness_min: float = 0.8
    correct_abstention_min: float = 0.95
    false_acceptance_max: float = 0.05
    claim_recovery_gain_min: float = 0.05
    provider_failure_rate_max: float = 0.05
    p95_latency_ms_max: int = 5_000


def build_book_learning_scorecard(
    *,
    retrieval_report: Mapping[str, Any],
    generation_report: Mapping[str, Any],
    generation_claim_report: Mapping[str, Any] | None = None,
    thresholds: ScorecardThresholds | None = None,
) -> dict[str, object]:
    """Build the four quality outcomes and two runtime outcomes."""

    thresholds = thresholds or ScorecardThresholds()
    retrieval_claims = _claim_metrics(retrieval_report)
    generation_metrics = _generation_metrics(generation_report)
    generation_claims = _claim_metrics(generation_claim_report or generation_report)
    runtime = _mapping(generation_report.get("runtime_metrics"))

    first_pass_coverage = _number(retrieval_claims.get("first_pass_claim_evidence_coverage"))
    answer_claim_coverage = _number(generation_metrics.get("answer_claim_coverage"))
    answer_correctness = _number(generation_metrics.get("answer_correctness"))
    correct_abstention = _first_number(
        generation_metrics.get("correct_abstention_rate"),
        generation_claims.get("correct_abstention_rate"),
    )
    false_acceptance = _first_number(
        generation_metrics.get("false_acceptance_rate"),
        generation_claims.get("false_acceptance_rate"),
    )
    recovery_gain = _number(generation_claims.get("claim_recovery_gain"))
    provider_failure_rate = _first_number(
        runtime.get("provider_failure_rate"), generation_metrics.get("provider_failure_rate")
    )
    p95_latency_ms = _number(runtime.get("p95_latency_ms"))

    quality_kpis = {
        "first_pass_claim_evidence_coverage": first_pass_coverage,
        "answer_claim_coverage": answer_claim_coverage,
        "answer_correctness": answer_correctness,
        "correct_abstention": correct_abstention,
        "false_acceptance": false_acceptance,
        "claim_recovery_gain": recovery_gain,
    }
    runtime_kpis = {
        "provider_failure_rate": provider_failure_rate,
        "p95_latency_ms": p95_latency_ms,
    }
    diagnostics = {
        "recall_at_k": _number(retrieval_report.get("recall_at_k")),
        "mrr": _number(retrieval_report.get("mrr")),
        "ndcg_at_k": _number(retrieval_report.get("ndcg_at_k")),
        "bundle_completeness_rate": _number(retrieval_claims.get("bundle_completeness_rate")),
        "context_precision": _number(generation_metrics.get("context_precision")),
        "context_recall": _number(generation_metrics.get("context_recall")),
        "faithfulness": _number(generation_metrics.get("faithfulness")),
        "citation_correctness": _number(generation_metrics.get("citation_correctness")),
        "unsupported_claim_rate": _number(generation_metrics.get("unsupported_claim_rate")),
        "answer_relevance": _number(generation_metrics.get("answer_relevance")),
    }
    return {
        "schema_version": 1,
        "stage": "book_learning_product_scorecard",
        "scope": {
            "dataset_status": "seed_manual",
            "production_claim_allowed": False,
            "online_policy_mutated": False,
            "chain_of_thought_required": False,
        },
        "quality_kpis": quality_kpis,
        "runtime_kpis": runtime_kpis,
        "diagnostics": diagnostics,
        "thresholds": {
            "first_pass_claim_coverage_min": thresholds.first_pass_claim_coverage_min,
            "answer_correctness_min": thresholds.answer_correctness_min,
            "correct_abstention_min": thresholds.correct_abstention_min,
            "false_acceptance_max": thresholds.false_acceptance_max,
            "claim_recovery_gain_min": thresholds.claim_recovery_gain_min,
            "provider_failure_rate_max": thresholds.provider_failure_rate_max,
            "p95_latency_ms_max": thresholds.p95_latency_ms_max,
        },
        "component_actions": _component_actions(quality_kpis, runtime_kpis, thresholds=thresholds),
    }


def compare_retrieval_strategies(
    reports: Mapping[str, Mapping[str, Any]], *, p95_latency_budget_ms: int = 3_000
) -> dict[str, object]:
    """Choose an offline candidate only when strategy receipts are comparable."""

    if len(reports) < 2:
        raise ValueError("strategy comparison requires at least two reports")
    invariants = {name: _strategy_invariants(report) for name, report in reports.items()}
    reference_name = next(iter(reports))
    reference = invariants[reference_name]
    mismatches = {
        name: [key for key, value in values.items() if value != reference.get(key)]
        for name, values in invariants.items()
        if values != reference
    }
    comparable = not mismatches and all(value is not None for value in reference.values())
    candidates: dict[str, dict[str, object]] = {}
    for name, report in reports.items():
        claim_metrics = _claim_metrics(report)
        latency = _mapping(report.get("latency_ms"))
        p95 = _number(latency.get("p95"))
        candidates[name] = {
            "first_pass_claim_evidence_coverage": _number(
                claim_metrics.get("first_pass_claim_evidence_coverage")
            ),
            "recall_at_k": _number(report.get("recall_at_k")),
            "mrr": _number(report.get("mrr")),
            "ndcg_at_k": _number(report.get("ndcg_at_k")),
            "p95_latency_ms": p95,
            "indexed_chunk_count": _number(_mapping(report.get("index")).get("chunk_count"))
            or _number(_mapping(report.get("chunking")).get("planned_chunk_count")),
            "source_dirty": bool(report.get("source_dirty")),
            "within_latency_budget": p95 is not None and p95 <= p95_latency_budget_ms,
        }

    has_claim_receipts = all(
        item["first_pass_claim_evidence_coverage"] is not None for item in candidates.values()
    )
    eligible = {
        name: item
        for name, item in candidates.items()
        if item["within_latency_budget"]
        and has_claim_receipts
        and not bool(item["source_dirty"])
    }
    recommended = None
    if comparable and eligible:
        recommended = max(
            eligible,
            key=lambda name: (
                _score_number(eligible[name]["first_pass_claim_evidence_coverage"]),
                _score_number(eligible[name]["recall_at_k"]),
                _score_number(eligible[name]["ndcg_at_k"]),
                _score_number(eligible[name]["mrr"]),
                -_score_number(eligible[name]["p95_latency_ms"]),
            ),
        )
    return {
        "comparison_protocol": {
            "same_corpus_embedding_top_k_required": True,
            "claim_coverage_required_for_selection": True,
            "clean_source_required_for_selection": True,
            "p95_latency_budget_ms": p95_latency_budget_ms,
            "online_policy_mutated": False,
        },
        "comparable": comparable,
        "invariant_mismatches": mismatches,
        "candidates": candidates,
        "recommended_strategy": recommended,
        "recommendation_status": (
            "offline_candidate" if recommended is not None else "insufficient_comparable_evidence"
        ),
    }


def _component_actions(
    quality: Mapping[str, float | None],
    runtime: Mapping[str, float | None],
    *,
    thresholds: ScorecardThresholds,
) -> dict[str, dict[str, str]]:
    return {
        "first_pass_coverage": _action(
            quality["first_pass_claim_evidence_coverage"],
            thresholds.first_pass_claim_coverage_min,
            component="chunking_embedding_or_reranker",
            comparison="min",
        ),
        "answer_correctness": _action(
            quality["answer_correctness"],
            thresholds.answer_correctness_min,
            component="answer_prompt_or_answer_gate",
            comparison="min",
        ),
        "abstention_safety": _paired_action(
            quality["correct_abstention"],
            quality["false_acceptance"],
            thresholds=thresholds,
        ),
        "recovery_value": _action(
            quality["claim_recovery_gain"],
            thresholds.claim_recovery_gain_min,
            component="query_decomposition_or_rewrite",
            comparison="min",
        ),
        "provider_reliability": _action(
            runtime["provider_failure_rate"],
            thresholds.provider_failure_rate_max,
            component="provider_timeout_retry_or_fallback",
            comparison="max",
        ),
        "latency": _action(
            runtime["p95_latency_ms"],
            float(thresholds.p95_latency_ms_max),
            component="context_budget_index_or_provider",
            comparison="max",
        ),
    }


def _action(
    value: float | None,
    threshold: float,
    *,
    component: str,
    comparison: str,
) -> dict[str, str]:
    if value is None:
        return {"status": "unmeasured", "component": component}
    passed = value >= threshold if comparison == "min" else value <= threshold
    return {"status": "hold" if passed else "optimize", "component": component}


def _paired_action(
    abstention: float | None,
    false_acceptance: float | None,
    *,
    thresholds: ScorecardThresholds,
) -> dict[str, str]:
    if abstention is None or false_acceptance is None:
        return {"status": "unmeasured", "component": "sufficiency_gate"}
    passed = (
        abstention >= thresholds.correct_abstention_min
        and false_acceptance <= thresholds.false_acceptance_max
    )
    return {"status": "hold" if passed else "optimize", "component": "sufficiency_gate"}


def _generation_metrics(report: Mapping[str, Any]) -> Mapping[str, Any]:
    outer = _mapping(report.get("metrics"))
    inner = _mapping(outer.get("metrics"))
    return inner or outer


def _claim_metrics(report: Mapping[str, Any]) -> Mapping[str, Any]:
    claim_report = _mapping(report.get("claim_evidence"))
    if claim_report:
        return _mapping(claim_report.get("metrics"))
    if report.get("stage") == "claim_evidence_sufficiency":
        return _mapping(report.get("metrics"))
    return {}


def _strategy_invariants(report: Mapping[str, Any]) -> dict[str, object]:
    provider = _mapping(report.get("provider"))
    return {
        "benchmark_id": report.get("benchmark_id"),
        "benchmark_revision": report.get("benchmark_revision"),
        "dataset_sha256": report.get("dataset_sha256"),
        "top_k": report.get("top_k"),
        "provider_model_id": provider.get("model_id"),
        "provider_model_revision": provider.get("model_revision"),
        "provider_dimensions": provider.get("dimensions"),
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _first_number(*values: Any) -> float | None:
    return next((value for item in values if (value := _number(item)) is not None), None)


def _score_number(value: object) -> float:
    return normalized if (normalized := _number(value)) is not None else 0.0


__all__ = [
    "ScorecardThresholds",
    "build_book_learning_scorecard",
    "compare_retrieval_strategies",
]
