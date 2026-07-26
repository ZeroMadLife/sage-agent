"""Dev-only calibration for retrieval abstention policies."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from core.knowledge.benchmark import KnowledgeBenchmarkQueryV2, evaluate_retrieval_v2
from core.knowledge.relevance import KnowledgeRelevancePolicy


@dataclass(frozen=True, slots=True)
class RelevanceCalibrationResult:
    policy: KnowledgeRelevancePolicy
    dev_baseline: dict[str, object]
    dev_calibrated: dict[str, object]
    test_baseline: dict[str, object]
    test_calibrated: dict[str, object]
    candidate_count: int


def calibrate_relevance_policy(
    queries: tuple[KnowledgeBenchmarkQueryV2, ...],
    raw_hits: dict[str, tuple[dict[str, Any], ...]],
    *,
    benchmark_id: str,
    benchmark_revision: str,
    corpus_revision: str,
    embedding_model: str,
    embedding_revision: str,
    supports_semantic_recall: bool,
    top_k: int,
    minimum_answerable_recall_ratio: float = 0.9,
) -> RelevanceCalibrationResult:
    """Select thresholds on dev only, then report untouched test performance."""

    dev = tuple(query for query in queries if query.split == "dev")
    test = tuple(query for query in queries if query.split == "test")
    if not dev or not test:
        raise ValueError("relevance calibration requires non-empty dev and test splits")
    if not any(not query.answerable for query in dev):
        raise ValueError("relevance calibration dev split requires unanswerable queries")
    query_ids = {query.query_id for query in queries}
    if set(raw_hits) != query_ids:
        raise ValueError("relevance calibration raw hits do not match benchmark queries")

    dev_baseline_report = evaluate_retrieval_v2(dev, _ranked_documents(dev, raw_hits), top_k=top_k)
    recall_floor = dev_baseline_report.recall_at_k * minimum_answerable_recall_ratio
    sparse_candidates = _score_candidates(raw_hits, dev, "sparse_score", limit=64)
    dense_candidates: tuple[float | None, ...]
    if supports_semantic_recall:
        sparse_candidates = (None, *sparse_candidates)
        dense_candidates = (None, *_score_candidates(raw_hits, dev, "dense_score", limit=32))
    else:
        dense_candidates = (None,)

    best: tuple[tuple[float, ...], KnowledgeRelevancePolicy, Any] | None = None
    candidate_count = 0
    for sparse_threshold in sparse_candidates:
        for dense_threshold in dense_candidates:
            if sparse_threshold is None and dense_threshold is None:
                continue
            candidate_count += 1
            policy = KnowledgeRelevancePolicy(
                benchmark_id=benchmark_id,
                benchmark_revision=benchmark_revision,
                corpus_revision=corpus_revision,
                embedding_model=embedding_model,
                embedding_revision=embedding_revision,
                top_k=top_k,
                min_sparse_score=sparse_threshold,
                min_dense_score=dense_threshold,
                minimum_answerable_recall_ratio=minimum_answerable_recall_ratio,
            )
            report = evaluate_retrieval_v2(
                dev,
                _ranked_documents(dev, raw_hits, policy=policy),
                top_k=top_k,
            )
            if report.recall_at_k + 1e-12 < recall_floor:
                continue
            objective = (
                report.unanswerable_accuracy,
                report.ndcg_at_k,
                report.recall_at_k,
                _strictness(sparse_threshold),
                _strictness(dense_threshold),
            )
            if best is None or objective > best[0]:
                best = (objective, policy, report)
    if best is None:
        raise ValueError("no relevance policy satisfies the answerable recall constraint")

    policy = best[1]
    test_baseline_report = evaluate_retrieval_v2(
        test, _ranked_documents(test, raw_hits), top_k=top_k
    )
    test_calibrated_report = evaluate_retrieval_v2(
        test, _ranked_documents(test, raw_hits, policy=policy), top_k=top_k
    )
    return RelevanceCalibrationResult(
        policy=policy,
        dev_baseline=_summary(dev_baseline_report),
        dev_calibrated=_summary(best[2]),
        test_baseline=_summary(test_baseline_report),
        test_calibrated=_summary(test_calibrated_report),
        candidate_count=candidate_count,
    )


def calibration_result_dict(result: RelevanceCalibrationResult) -> dict[str, object]:
    return {
        "policy": result.policy.to_dict(),
        "dev_baseline": result.dev_baseline,
        "dev_calibrated": result.dev_calibrated,
        "test_baseline": result.test_baseline,
        "test_calibrated": result.test_calibrated,
        "candidate_count": result.candidate_count,
    }


def report_hits(report: dict[str, Any]) -> dict[str, tuple[dict[str, Any], ...]]:
    cases = report.get("cases")
    if not isinstance(cases, list):
        raise ValueError("benchmark report cases are required")
    parsed: dict[str, tuple[dict[str, Any], ...]] = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("hits"), list):
            raise ValueError("benchmark report cases require raw hits")
        query_id = str(case.get("query_id", ""))
        if not query_id or query_id in parsed:
            raise ValueError("benchmark report query ids must be unique")
        hits: list[dict[str, Any]] = []
        for hit in case["hits"]:
            if not isinstance(hit, dict) or "passage_id" not in hit:
                raise ValueError("benchmark report contains an invalid raw hit")
            hits.append(hit)
        parsed[query_id] = tuple(hits)
    return parsed


def _ranked_documents(
    queries: Iterable[KnowledgeBenchmarkQueryV2],
    raw_hits: dict[str, tuple[dict[str, Any], ...]],
    *,
    policy: KnowledgeRelevancePolicy | None = None,
) -> dict[str, tuple[str, ...]]:
    ranked: dict[str, tuple[str, ...]] = {}
    for query in queries:
        hits = raw_hits.get(query.query_id, ())
        documents = (
            str(hit["passage_id"])
            for hit in hits
            if policy is None
            or policy.accepts(
                sparse_score=_score(hit.get("sparse_score")),
                dense_score=_score(hit.get("dense_score")),
            )
        )
        ranked[query.query_id] = tuple(dict.fromkeys(documents))
    return ranked


def _score_candidates(
    raw_hits: dict[str, tuple[dict[str, Any], ...]],
    queries: tuple[KnowledgeBenchmarkQueryV2, ...],
    field: str,
    *,
    limit: int | None,
) -> tuple[float | None, ...]:
    values = sorted(
        {
            value
            for query in queries
            for hit in raw_hits.get(query.query_id, ())
            if (value := _score(hit.get(field))) is not None and value >= 0.0
        }
    )
    if not values:
        return (None,)
    boundaries = {0.0, math.nextafter(values[-1], math.inf)}
    boundaries.update(math.nextafter(value, math.inf) for value in values)
    ordered = sorted(boundaries)
    if limit is not None and len(ordered) > limit:
        indexes = {round(index * (len(ordered) - 1) / (limit - 1)) for index in range(limit)}
        ordered = [ordered[index] for index in sorted(indexes)]
    return tuple(ordered)


def _score(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ValueError("benchmark report contains an invalid score")
    score = float(value)
    if not math.isfinite(score):
        raise ValueError("benchmark report contains a non-finite score")
    return score


def _strictness(value: float | None) -> float:
    return -1.0 if value is None else value


def _summary(report: Any) -> dict[str, object]:
    raw = asdict(report)
    return {
        key: raw[key]
        for key in (
            "query_count",
            "answerable_query_count",
            "unanswerable_query_count",
            "recall_at_k",
            "precision_at_k",
            "mrr",
            "ndcg_at_k",
            "hit_rate",
            "unanswerable_accuracy",
        )
    }
