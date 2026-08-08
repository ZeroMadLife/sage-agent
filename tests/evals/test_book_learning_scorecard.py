from __future__ import annotations

from evals.book_learning_scorecard import (
    build_book_learning_scorecard,
    compare_retrieval_strategies,
)


def _retrieval(strategy: str, *, coverage: float, p95: float, ndcg: float) -> dict:
    return {
        "benchmark_id": "book-v1",
        "benchmark_revision": "r1",
        "dataset_sha256": "sha",
        "top_k": 10,
        "provider": {"model_id": "embed", "model_revision": "r1", "dimensions": 384},
        "ablation_policy": {"strategy": strategy},
        "recall_at_k": coverage,
        "mrr": ndcg - 0.05,
        "ndcg_at_k": ndcg,
        "latency_ms": {"p95": p95},
        "chunking": {"planned_chunk_count": 100},
        "source_dirty": False,
        "claim_evidence": {
            "metrics": {
                "first_pass_claim_evidence_coverage": coverage,
                "bundle_completeness_rate": coverage,
            }
        },
    }


def test_scorecard_keeps_core_outcomes_and_diagnostics_separate() -> None:
    retrieval = _retrieval("contextual_chunk", coverage=0.73, p95=1_600, ndcg=0.58)
    generation = {
        "metrics": {
            "metrics": {
                "answer_claim_coverage": 0.62,
                "answer_correctness": 0.57,
                "correct_abstention_rate": 1.0,
                "false_acceptance_rate": 0.0,
                "faithfulness": 1.0,
                "citation_correctness": 1.0,
                "unsupported_claim_rate": 0.0,
            }
        },
        "runtime_metrics": {"provider_failure_rate": 0.21, "p95_latency_ms": 120_000},
    }
    claim_report = {"stage": "claim_evidence_sufficiency", "metrics": {"claim_recovery_gain": 0.0}}

    result = build_book_learning_scorecard(
        retrieval_report=retrieval,
        generation_report=generation,
        generation_claim_report=claim_report,
    )

    assert result["quality_kpis"] == {
        "first_pass_claim_evidence_coverage": 0.73,
        "answer_claim_coverage": 0.62,
        "answer_correctness": 0.57,
        "correct_abstention": 1.0,
        "false_acceptance": 0.0,
        "claim_recovery_gain": 0.0,
    }
    assert result["diagnostics"]["faithfulness"] == 1.0
    assert result["component_actions"]["first_pass_coverage"]["component"] == (
        "chunking_embedding_or_reranker"
    )
    assert result["component_actions"]["recovery_value"] == {
        "status": "optimize",
        "component": "query_decomposition_or_rewrite",
    }
    assert result["component_actions"]["abstention_safety"]["status"] == "hold"
    assert result["component_actions"]["provider_reliability"]["status"] == "optimize"


def test_strategy_comparison_rejects_slow_parent_child_and_selects_contextual() -> None:
    result = compare_retrieval_strategies(
        {
            "baseline": _retrieval("baseline", coverage=0.70, p95=2_300, ndcg=0.56),
            "contextual_chunk": _retrieval("contextual_chunk", coverage=0.75, p95=1_600, ndcg=0.58),
            "parent_child": _retrieval("parent_child", coverage=0.75, p95=6_300, ndcg=0.65),
        }
    )

    assert result["comparable"] is True
    assert result["recommended_strategy"] == "contextual_chunk"
    assert result["candidates"]["parent_child"]["within_latency_budget"] is False
    assert result["recommendation_status"] == "offline_candidate"


def test_strategy_comparison_requires_same_dataset_embedding_and_top_k() -> None:
    baseline = _retrieval("baseline", coverage=0.70, p95=2_300, ndcg=0.56)
    candidate = _retrieval("contextual_chunk", coverage=0.75, p95=1_600, ndcg=0.58)
    candidate["top_k"] = 8

    result = compare_retrieval_strategies({"baseline": baseline, "candidate": candidate})

    assert result["comparable"] is False
    assert result["recommended_strategy"] is None
    assert result["invariant_mismatches"] == {"candidate": ["top_k"]}


def test_strategy_comparison_does_not_recommend_dirty_receipts() -> None:
    baseline = _retrieval("baseline", coverage=0.70, p95=2_300, ndcg=0.56)
    candidate = _retrieval("contextual_chunk", coverage=0.75, p95=1_600, ndcg=0.58)
    baseline["source_dirty"] = True
    candidate["source_dirty"] = True

    result = compare_retrieval_strategies({"baseline": baseline, "candidate": candidate})

    assert result["comparable"] is True
    assert result["recommended_strategy"] is None
    assert result["recommendation_status"] == "insufficient_comparable_evidence"
