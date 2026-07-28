from __future__ import annotations

from copy import deepcopy

from core.knowledge.eval_runner import compare_retrieval_ablation_reports


def _report(*, strategy: str) -> dict:
    return {
        "dataset": {"dataset_id": "dataset", "dataset_revision": "revision"},
        "inputs": {"cases_sha256": "sha256:cases"},
        "backend": "postgres-tsvector+pgvector-exact",
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
