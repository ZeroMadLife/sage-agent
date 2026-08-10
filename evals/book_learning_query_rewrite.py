"""Evaluate bounded pre-retrieval query rewrite without changing the online path."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from core.knowledge.benchmark import KnowledgeBenchmarkQueryV2, evaluate_retrieval_v2
from evals.book_learning_claims import (
    ClaimEvidenceEvalCase,
    ClaimEvidenceGoldCase,
    evaluate_claim_evidence,
)


@dataclass(frozen=True, slots=True)
class QueryRewriteObservation:
    """Ranked passage observations for one original query and its bounded rewrite."""

    query_id: str
    original_passage_ids: tuple[str, ...]
    rewrite_passage_ids: tuple[str, ...]
    rewrite_preserved_intent: bool | None = None

    def __post_init__(self) -> None:
        if not self.query_id.strip():
            raise ValueError("query rewrite observation requires a query id")
        if not self.rewrite_passage_ids:
            raise ValueError("query rewrite observation requires rewrite passages")


def evaluate_query_rewrite_variants(
    queries: tuple[KnowledgeBenchmarkQueryV2, ...],
    claim_gold: tuple[ClaimEvidenceGoldCase, ...],
    observations: tuple[QueryRewriteObservation, ...],
    *,
    top_k: int,
    rank_constant: int = 60,
) -> dict[str, object]:
    """Compare original-only, rewrite-only and bounded original+rewrite RRF."""

    if not queries or not claim_gold or not observations:
        raise ValueError("query rewrite evaluation requires queries, claim gold and observations")
    if top_k < 1:
        raise ValueError("query rewrite top_k must be positive")
    if rank_constant < 1:
        raise ValueError("query rewrite rank constant must be positive")
    observation_ids = [item.query_id for item in observations]
    if len(observation_ids) != len(set(observation_ids)):
        raise ValueError("query rewrite observation ids must be unique")
    query_by_id = {item.query_id: item for item in queries}
    gold_by_id = {item.query_id: item for item in claim_gold}
    if set(observation_ids) != set(query_by_id) or set(observation_ids) != set(gold_by_id):
        raise ValueError("query rewrite inputs must contain the same query ids")

    ranked: dict[str, dict[str, tuple[str, ...]]] = {
        "original_only": {},
        "rewrite_only": {},
        "original_plus_rewrite": {},
    }
    for observation in observations:
        original = _dedupe(observation.original_passage_ids)[:top_k]
        rewrite = _dedupe(observation.rewrite_passage_ids)[:top_k]
        ranked["original_only"][observation.query_id] = original
        ranked["rewrite_only"][observation.query_id] = rewrite
        ranked["original_plus_rewrite"][observation.query_id] = fuse_ranked_passages(
            (original, rewrite),
            top_k=top_k,
            rank_constant=rank_constant,
        )

    ordered_queries = tuple(query_by_id[item.query_id] for item in observations)
    ordered_gold = tuple(gold_by_id[item.query_id] for item in observations)
    variants: dict[str, dict[str, object]] = {}
    for name, results in ranked.items():
        retrieval = evaluate_retrieval_v2(ordered_queries, results, top_k=top_k)
        claim_cases = tuple(
            ClaimEvidenceEvalCase(
                query_id=item.query_id,
                first_round_passage_ids=results[item.query_id],
                final_passage_ids=results[item.query_id],
                final_decision=None,
            )
            for item in observations
        )
        variants[name] = {
            "retrieval": asdict(retrieval),
            "claim_evidence": evaluate_claim_evidence(ordered_gold, claim_cases),
        }

    labels = [
        item.rewrite_preserved_intent
        for item in observations
        if item.rewrite_preserved_intent is not None
    ]
    return {
        "schema_version": 1,
        "stage": "pre_retrieval_query_rewrite_ablation",
        "protocol": {
            "variants": list(ranked),
            "fusion": f"rrf-k{rank_constant}",
            "top_k": top_k,
            "original_query_preserved_in_expansion": True,
            "online_default_changed": False,
        },
        "query_count": len(observations),
        "rewrite_intent_drift_rate": (
            round(sum(value is False for value in labels) / len(labels), 4) if labels else None
        ),
        "variants": variants,
        "deltas_vs_original": {
            name: _delta(variants["original_only"], variants[name])
            for name in ("rewrite_only", "original_plus_rewrite")
        },
    }


def _delta(baseline: dict[str, object], candidate: dict[str, object]) -> dict[str, float]:
    baseline_retrieval = baseline["retrieval"]
    candidate_retrieval = candidate["retrieval"]
    baseline_claims = baseline["claim_evidence"]
    candidate_claims = candidate["claim_evidence"]
    assert isinstance(baseline_retrieval, dict)
    assert isinstance(candidate_retrieval, dict)
    assert isinstance(baseline_claims, dict)
    assert isinstance(candidate_claims, dict)
    baseline_claim_metrics = baseline_claims["metrics"]
    candidate_claim_metrics = candidate_claims["metrics"]
    assert isinstance(baseline_claim_metrics, dict)
    assert isinstance(candidate_claim_metrics, dict)
    return {
        "recall_at_k": _difference(
            candidate_retrieval["recall_at_k"], baseline_retrieval["recall_at_k"]
        ),
        "mrr": _difference(candidate_retrieval["mrr"], baseline_retrieval["mrr"]),
        "ndcg_at_k": _difference(candidate_retrieval["ndcg_at_k"], baseline_retrieval["ndcg_at_k"]),
        "first_pass_claim_evidence_coverage": _difference(
            candidate_claim_metrics["first_pass_claim_evidence_coverage"],
            baseline_claim_metrics["first_pass_claim_evidence_coverage"],
        ),
    }


def _difference(candidate: object, baseline: object) -> float:
    return round(_numeric(candidate) - _numeric(baseline), 4)


def _numeric(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("query rewrite metric must be numeric")
    return float(value)


def fuse_ranked_passages(
    routes: tuple[tuple[str, ...], ...],
    *,
    top_k: int,
    rank_constant: int,
) -> tuple[str, ...]:
    """Fuse independent ranked passage routes with deterministic RRF."""

    if not routes:
        return ()
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for route in routes:
        for rank, passage_id in enumerate(route, start=1):
            scores[passage_id] = scores.get(passage_id, 0.0) + 1.0 / (rank_constant + rank)
            first_seen.setdefault(passage_id, len(first_seen))
    return tuple(
        passage_id
        for passage_id, _score in sorted(
            scores.items(),
            key=lambda item: (-item[1], first_seen[item[0]], item[0]),
        )[:top_k]
    )


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value.strip()))


__all__ = [
    "QueryRewriteObservation",
    "evaluate_query_rewrite_variants",
    "fuse_ranked_passages",
]
