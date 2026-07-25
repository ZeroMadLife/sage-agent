"""Deterministic retrieval metrics for versioned knowledge indexes."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_V2_CATEGORIES = {
    "legacy_migrated",
    "real_user",
    "paraphrase",
    "hard_negative",
    "multi_document",
    "unanswerable",
}
_V2_SPLITS = {"smoke", "dev", "test"}


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


@dataclass(frozen=True, slots=True)
class KnowledgeRelevanceJudgment:
    document_id: str
    relevance: int

    def __post_init__(self) -> None:
        if not self.document_id.strip():
            raise ValueError("relevance judgment requires a document id")
        if isinstance(self.relevance, bool) or self.relevance not in {1, 2, 3}:
            raise ValueError("relevance must be an integer between 1 and 3")


@dataclass(frozen=True, slots=True)
class KnowledgeBenchmarkQueryV2:
    query_id: str
    query: str
    category: str
    split: str
    answerable: bool
    relevant: tuple[KnowledgeRelevanceJudgment, ...]
    provenance: str = ""
    required_claims: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.query_id.strip() or not self.query.strip():
            raise ValueError("benchmark query id and text are required")
        if self.category not in _V2_CATEGORIES:
            raise ValueError(f"unsupported benchmark category: {self.category}")
        if self.split not in _V2_SPLITS:
            raise ValueError(f"unsupported benchmark split: {self.split}")
        if self.answerable and not self.relevant:
            raise ValueError("answerable query requires relevance judgments")
        if not self.answerable and self.relevant:
            raise ValueError("unanswerable query cannot have relevance judgments")
        ids = [item.document_id for item in self.relevant]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark query has duplicate relevance judgments")


@dataclass(frozen=True, slots=True)
class KnowledgeBenchmarkCaseV2:
    query_id: str
    category: str
    split: str
    answerable: bool
    retrieved_documents: tuple[str, ...]
    recall_at_k: float
    precision_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float
    hit: bool
    correct_no_answer: bool


@dataclass(frozen=True, slots=True)
class KnowledgeBenchmarkSliceV2:
    query_count: int
    answerable_query_count: int
    unanswerable_query_count: int
    recall_at_k: float
    precision_at_k: float
    mrr: float
    ndcg_at_k: float
    hit_rate: float
    unanswerable_accuracy: float


@dataclass(frozen=True, slots=True)
class KnowledgeBenchmarkReportV2:
    query_count: int
    top_k: int
    answerable_query_count: int
    unanswerable_query_count: int
    recall_at_k: float
    precision_at_k: float
    mrr: float
    ndcg_at_k: float
    hit_rate: float
    unanswerable_accuracy: float
    categories: dict[str, KnowledgeBenchmarkSliceV2]
    splits: dict[str, KnowledgeBenchmarkSliceV2]
    cases: tuple[KnowledgeBenchmarkCaseV2, ...]


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


def evaluate_retrieval_v2(
    queries: tuple[KnowledgeBenchmarkQueryV2, ...],
    ranked_documents: dict[str, tuple[str, ...]],
    *,
    top_k: int,
) -> KnowledgeBenchmarkReportV2:
    """Evaluate graded passage retrieval and explicit no-answer behavior."""

    if not queries:
        raise ValueError("knowledge benchmark requires queries")
    if top_k < 1:
        raise ValueError("knowledge benchmark top_k must be positive")
    ids = [item.query_id for item in queries]
    if len(ids) != len(set(ids)):
        raise ValueError("knowledge benchmark query ids must be unique")

    cases: list[KnowledgeBenchmarkCaseV2] = []
    for query in queries:
        retrieved = tuple(dict.fromkeys(ranked_documents.get(query.query_id, ())))[:top_k]
        relevance = {item.document_id: item.relevance for item in query.relevant}
        matched = tuple(item for item in retrieved if item in relevance)
        first_rank = next(
            (rank for rank, item in enumerate(retrieved, start=1) if item in relevance),
            None,
        )
        dcg = sum(
            ((2 ** relevance[item]) - 1) / math.log2(rank + 1)
            for rank, item in enumerate(retrieved, start=1)
            if item in relevance
        )
        ideal = sorted(relevance.values(), reverse=True)[:top_k]
        ideal_dcg = sum(
            ((2**grade) - 1) / math.log2(rank + 1)
            for rank, grade in enumerate(ideal, start=1)
        )
        cases.append(
            KnowledgeBenchmarkCaseV2(
                query_id=query.query_id,
                category=query.category,
                split=query.split,
                answerable=query.answerable,
                retrieved_documents=retrieved,
                recall_at_k=(len(matched) / len(relevance) if relevance else 0.0),
                precision_at_k=(len(matched) / top_k if relevance else 0.0),
                reciprocal_rank=(1.0 / first_rank if first_rank is not None else 0.0),
                ndcg_at_k=(dcg / ideal_dcg if ideal_dcg else 0.0),
                hit=bool(matched),
                correct_no_answer=not query.answerable and not retrieved,
            )
        )

    summary = _summarize_v2(cases)
    categories = {
        category: _summarize_v2([item for item in cases if item.category == category])
        for category in sorted({item.category for item in cases})
    }
    splits = {
        split: _summarize_v2([item for item in cases if item.split == split])
        for split in sorted({item.split for item in cases})
    }
    return KnowledgeBenchmarkReportV2(
        query_count=summary.query_count,
        top_k=top_k,
        answerable_query_count=summary.answerable_query_count,
        unanswerable_query_count=summary.unanswerable_query_count,
        recall_at_k=summary.recall_at_k,
        precision_at_k=summary.precision_at_k,
        mrr=summary.mrr,
        ndcg_at_k=summary.ndcg_at_k,
        hit_rate=summary.hit_rate,
        unanswerable_accuracy=summary.unanswerable_accuracy,
        categories=categories,
        splits=splits,
        cases=tuple(cases),
    )


def load_benchmark_v2(path: Path) -> tuple[KnowledgeBenchmarkQueryV2, ...]:
    """Load the strict, line-addressable Benchmark v2 dataset."""

    queries: list[KnowledgeBenchmarkQueryV2] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            queries.append(_query_v2(raw))
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
            raise ValueError(f"invalid benchmark v2 record at line {line_number}") from exc
    ids = [item.query_id for item in queries]
    if len(ids) != len(set(ids)):
        raise ValueError("benchmark v2 query ids must be unique")
    return tuple(queries)


def passage_id(source: str, section: str) -> str:
    normalized_source = source.strip()
    normalized_section = " / ".join(part.strip() for part in section.split("/") if part.strip())
    if not normalized_source or not normalized_section or "#" in normalized_source:
        raise ValueError("benchmark passage requires a source and section")
    return f"{normalized_source}#{normalized_section}"


def _summarize_v2(cases: list[KnowledgeBenchmarkCaseV2]) -> KnowledgeBenchmarkSliceV2:
    answerable = [item for item in cases if item.answerable]
    unanswerable = [item for item in cases if not item.answerable]

    def average(name: str, values: list[KnowledgeBenchmarkCaseV2]) -> float:
        return sum(float(getattr(item, name)) for item in values) / len(values) if values else 0.0

    return KnowledgeBenchmarkSliceV2(
        query_count=len(cases),
        answerable_query_count=len(answerable),
        unanswerable_query_count=len(unanswerable),
        recall_at_k=average("recall_at_k", answerable),
        precision_at_k=average("precision_at_k", answerable),
        mrr=average("reciprocal_rank", answerable),
        ndcg_at_k=average("ndcg_at_k", answerable),
        hit_rate=average("hit", answerable),
        unanswerable_accuracy=average("correct_no_answer", unanswerable),
    )


def _query_v2(raw: Any) -> KnowledgeBenchmarkQueryV2:
    if not isinstance(raw, dict):
        raise TypeError("benchmark record must be an object")
    allowed = {
        "id",
        "query",
        "category",
        "split",
        "answerable",
        "provenance",
        "relevant_passages",
        "required_claims",
        "forbidden_claims",
    }
    if set(raw) != allowed:
        raise ValueError("benchmark record fields do not match the v2 contract")
    passages = raw["relevant_passages"]
    if not isinstance(passages, list):
        raise TypeError("relevant_passages must be a list")
    judgments: list[KnowledgeRelevanceJudgment] = []
    for item in passages:
        if not isinstance(item, dict) or set(item) != {"source", "section", "relevance"}:
            raise TypeError("invalid relevant passage")
        judgments.append(
            KnowledgeRelevanceJudgment(
                passage_id(str(item["source"]), str(item["section"])),
                item["relevance"],
            )
        )
    required_claims = raw["required_claims"]
    forbidden_claims = raw["forbidden_claims"]
    if not isinstance(required_claims, list) or not isinstance(forbidden_claims, list):
        raise TypeError("benchmark claims must be lists")
    if not isinstance(raw["answerable"], bool):
        raise TypeError("answerable must be boolean")
    return KnowledgeBenchmarkQueryV2(
        query_id=str(raw["id"]),
        query=str(raw["query"]),
        category=str(raw["category"]),
        split=str(raw["split"]),
        answerable=raw["answerable"],
        relevant=tuple(judgments),
        provenance=str(raw["provenance"]),
        required_claims=tuple(str(item) for item in required_claims),
        forbidden_claims=tuple(str(item) for item in forbidden_claims),
    )
