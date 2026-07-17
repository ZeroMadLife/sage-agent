"""Deterministic retrieval metrics for versioned knowledge indexes."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KnowledgeGoldenQuery:
    query_id: str
    query: str
    category: str
    relevant_sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class KnowledgeBenchmarkCase:
    query_id: str
    retrieved_sources: tuple[str, ...]
    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float
    hit: bool


@dataclass(frozen=True, slots=True)
class KnowledgeBenchmarkReport:
    query_count: int
    top_k: int
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    hit_rate: float
    cases: tuple[KnowledgeBenchmarkCase, ...]


def evaluate_retrieval(
    golden_queries: tuple[KnowledgeGoldenQuery, ...],
    ranked_sources: dict[str, tuple[str, ...]],
    *,
    top_k: int,
) -> KnowledgeBenchmarkReport:
    if not golden_queries:
        raise ValueError("knowledge benchmark requires golden queries")
    if top_k < 1:
        raise ValueError("knowledge benchmark top_k must be positive")
    cases: list[KnowledgeBenchmarkCase] = []
    for golden in golden_queries:
        relevant = set(golden.relevant_sources)
        if not relevant:
            raise ValueError(f"golden query {golden.query_id} has no relevant sources")
        retrieved = tuple(dict.fromkeys(ranked_sources.get(golden.query_id, ())))[:top_k]
        matched = relevant.intersection(retrieved)
        first_rank = next(
            (rank for rank, source in enumerate(retrieved, start=1) if source in relevant),
            None,
        )
        dcg = sum(
            1.0 / math.log2(rank + 1)
            for rank, source in enumerate(retrieved, start=1)
            if source in relevant
        )
        ideal_count = min(len(relevant), top_k)
        ideal_dcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
        cases.append(
            KnowledgeBenchmarkCase(
                query_id=golden.query_id,
                retrieved_sources=retrieved,
                recall_at_k=len(matched) / len(relevant),
                reciprocal_rank=(1.0 / first_rank if first_rank is not None else 0.0),
                ndcg_at_k=(dcg / ideal_dcg if ideal_dcg else 0.0),
                hit=bool(matched),
            )
        )
    count = len(cases)
    return KnowledgeBenchmarkReport(
        query_count=count,
        top_k=top_k,
        recall_at_k=sum(item.recall_at_k for item in cases) / count,
        mrr=sum(item.reciprocal_rank for item in cases) / count,
        ndcg_at_k=sum(item.ndcg_at_k for item in cases) / count,
        hit_rate=sum(item.hit for item in cases) / count,
        cases=tuple(cases),
    )
