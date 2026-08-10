from __future__ import annotations

from copy import deepcopy

from core.knowledge.eval_runner import compare_retrieval_ablation_reports


def _report(*, strategy: str) -> dict:
    return {
        "dataset": {"dataset_id": "dataset", "dataset_revision": "revision"},
        "inputs": {"cases_sha256": "sha256:cases"},
        "backend": "postgres-tsvector+pgvector-exact",
        "backend_schema_revision": "20260806_rag_described_parent_child_v1",
        "provider": {"model_id": "semantic", "model_revision": "model-revision"},
        "parameters": {
            "top_k": 10,
            "candidate_k": 50,
            "token_budget": 3000,
            "evaluation_splits": ["dev", "calibration"],
        },
        "ablation": {
            "strategy": strategy,
            "corpus_profile": {"semantic_boundary_eligible_block_count": 0},
        },
        "index": {"active_chunk_count": 50},
        "storage": {"workspace_row_bytes": 1000},
        "routes": {
            "hybrid": {
                "retrieval": {"recall_at_k": 0.80},
                "ranking": {"ndcg_at_k": 0.70, "mrr": 0.68},
                "citation": {"support_rate": 1.0},
                "generation": {"required_claim_token_recall": 0.60},
                "system": {
                    "latency_ms": {"p95": 20.0},
                    "estimated_cost_usd": 0.0,
                },
                "failures": {"false_acceptance": 1},
            }
        },
    }


def test_contextual_ablation_requires_target_gain_and_common_safety_gates() -> None:
    baseline = _report(strategy="baseline")
    candidate = deepcopy(baseline)
    candidate["ablation"]["strategy"] = "contextual_chunk"
    candidate["routes"]["hybrid"]["ranking"]["ndcg_at_k"] = 0.72
    candidate["routes"]["hybrid"]["system"]["latency_ms"]["p95"] = 40.0
    candidate["storage"]["workspace_row_bytes"] = 1200

    comparison = compare_retrieval_ablation_reports(baseline, candidate)

    assert comparison["overall_passed"] is True
    assert comparison["target_metric"]["name"] == "max_recall_or_ndcg_delta"
    assert comparison["target_metric"]["delta"] == 0.02
    assert all(gate["passed"] is True for gate in comparison["gates"].values())


def test_described_parent_child_can_attribute_claim_or_ranking_gain() -> None:
    baseline = _report(strategy="baseline")
    candidate = deepcopy(baseline)
    candidate["ablation"]["strategy"] = "described_parent_child"
    candidate["ablation"]["corpus_profile"]["semantic_boundary_eligible_block_count"] = 2
    candidate["routes"]["hybrid"]["generation"]["required_claim_token_recall"] = 0.62
    candidate["index"]["active_chunk_count"] = 100
    candidate["storage"]["workspace_row_bytes"] = 2000

    comparison = compare_retrieval_ablation_reports(baseline, candidate)

    assert comparison["overall_passed"] is True
    assert comparison["target_metric"] == {
        "name": "max_recall_ndcg_or_claim_delta",
        "minimum_delta": 0.01,
        "delta": 0.02,
        "passed": True,
    }


def test_described_parent_child_fails_when_long_block_path_was_not_exercised() -> None:
    baseline = _report(strategy="baseline")
    candidate = deepcopy(baseline)
    candidate["ablation"]["strategy"] = "described_parent_child"
    candidate["routes"]["hybrid"]["ranking"]["ndcg_at_k"] = 0.72

    comparison = compare_retrieval_ablation_reports(baseline, candidate)

    assert comparison["overall_passed"] is False
    assert comparison["gates"]["strategy_exercised"] == {
        "minimum": 1,
        "actual": 0,
        "passed": False,
    }


def test_semantic_boundary_fails_closed_when_frozen_corpus_has_no_eligible_block() -> None:
    baseline = _report(strategy="baseline")
    candidate = deepcopy(baseline)
    candidate["ablation"]["strategy"] = "semantic_boundary"
    candidate["routes"]["hybrid"]["ranking"]["ndcg_at_k"] = 0.75

    comparison = compare_retrieval_ablation_reports(baseline, candidate)

    assert comparison["overall_passed"] is False
    assert comparison["gates"]["strategy_exercised"] == {
        "minimum": 1,
        "actual": 0,
        "passed": False,
    }
