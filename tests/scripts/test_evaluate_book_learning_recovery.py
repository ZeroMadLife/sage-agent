from __future__ import annotations

from pathlib import Path

import pytest

from core.knowledge.benchmark import load_benchmark_v2
from scripts.evaluate_book_learning_recovery import _load_plans, _percentile, _to_eval_case


def test_committed_recovery_plans_reference_book_benchmark_cases() -> None:
    repo_root = Path(__file__).parents[2]
    queries = load_benchmark_v2(repo_root / "evals" / "book_learning_benchmark_v1.jsonl")

    plans = _load_plans(
        repo_root / "evals" / "book_learning_recovery_v1.jsonl",
        {query.query_id: query for query in queries},
    )

    assert len(plans) == 4
    assert all(plan["provenance"] == "oracle_manual_rewrite" for plan in plans)
    assert all(plan["rewrite_preserved_intent"] for plan in plans)
    assert all(len(plan["rewrite_queries"]) <= 2 for plan in plans)


def test_recovery_plans_reject_more_than_two_queries_and_non_boolean_labels(
    tmp_path: Path,
) -> None:
    queries = {
        "q1": object(),
    }
    path = tmp_path / "plans.jsonl"
    path.write_text(
        '{"case_id":"q1","rewrite_queries":["a","b","c"],'
        '"rewrite_preserved_intent":"false","provenance":"oracle_manual_rewrite"}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="invalid recovery plan"):
        _load_plans(path, queries)


def test_recovery_case_unions_new_evidence_and_uses_parallel_latency() -> None:
    repo_root = Path(__file__).parents[2]
    query = next(
        item
        for item in load_benchmark_v2(repo_root / "evals" / "book_learning_benchmark_v1.jsonl")
        if item.query_id == "book-cross-001"
    )
    gold = [item.document_id for item in query.relevant]
    by_id = {
        query.query_id: {
            "latency_ms": 100,
            "hits": [{"passage_id": gold[0]}],
        },
        "rewrite-a": {
            "latency_ms": 240,
            "hits": [{"passage_id": gold[1]}],
        },
        "rewrite-b": {
            "latency_ms": 180,
            "hits": [{"passage_id": "noise#section"}],
        },
    }

    case = _to_eval_case(
        query,
        {
            "rewrite_preserved_intent": True,
        },
        by_id,
        ("rewrite-a", "rewrite-b"),
    )

    assert set(case.final_evidence) >= set(gold)
    assert case.final_claims == query.required_claims
    assert case.recovery_latency_ms == 240


def test_recovery_receipt_percentile_uses_nearest_rank() -> None:
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0.50) == 2.0
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0.95) == 4.0
