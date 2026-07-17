from __future__ import annotations

import json
from pathlib import Path

from core.knowledge.benchmark import KnowledgeGoldenQuery, evaluate_retrieval


def test_retrieval_metrics_use_source_relevance_and_rank() -> None:
    golden = (
        KnowledgeGoldenQuery("q1", "memory", "memory", ("memory.md",)),
        KnowledgeGoldenQuery("q2", "context", "context", ("context.md", "prompt.md")),
    )
    report = evaluate_retrieval(
        golden,
        {
            "q1": ("other.md", "memory.md"),
            "q2": ("context.md", "other.md"),
        },
        top_k=2,
    )

    assert report.query_count == 2
    assert report.recall_at_k == 0.75
    assert report.mrr == 0.75
    assert 0.0 < report.ndcg_at_k < 1.0
    assert report.hit_rate == 1.0


def test_committed_golden_query_set_starts_with_fifty_unique_cases() -> None:
    path = Path(__file__).parents[3] / "evals" / "knowledge_golden_queries.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert len(payload) == 50
    assert len({item["id"] for item in payload}) == 50
    assert all(item["query"].strip() for item in payload)
    assert all(item["category"].strip() for item in payload)
    assert all(item["relevant_sources"] for item in payload)
