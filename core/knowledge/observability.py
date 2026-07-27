"""Privacy-bounded retrieval traces shared by Knowledge index backends."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

RetrievalFailureType = Literal[
    "none",
    "ingestion",
    "retrieval",
    "gate_rejected",
    "system",
]
GateDecision = Literal["not_configured", "not_evaluated", "accepted", "rejected"]
RankedCandidate = tuple[
    str,
    float,
    int | None,
    float | None,
    int | None,
    float | None,
]


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievalObservabilityConfig:
    """Enable keyed, content-free retrieval traces for one backend."""

    enabled: bool = False
    hmac_key: str = field(default="", repr=False)
    max_candidates: int = 50

    def __post_init__(self) -> None:
        if self.enabled and len(self.hmac_key.encode("utf-8")) < 32:
            raise ValueError("Knowledge retrieval observability HMAC key must be at least 32 bytes")
        if isinstance(self.max_candidates, bool) or not 1 <= self.max_candidates <= 200:
            raise ValueError("Knowledge retrieval trace candidate limit must be between 1 and 200")

    def fingerprint(self, value: str) -> str:
        if not self.enabled:
            raise RuntimeError("Knowledge retrieval observability is disabled")
        digest = hmac.new(
            self.hmac_key.encode("utf-8"),
            value.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"hmac-sha256:{digest}"


@dataclass(frozen=True, slots=True)
class KnowledgeRetrievalTrace:
    """One retrieval run without query text, chunk text, or exception messages."""

    run_id: str
    query_hash: str
    query_length: int
    round_index: int
    rewrite_hash: str | None
    retrieval_mode: str
    corpus_revision: str
    embedding_model: str
    embedding_revision: str
    top_k: int
    candidate_limit: int
    candidate_count: int
    returned_count: int
    candidates_json: str
    result_coverage: float
    gate_decision: GateDecision
    latency_ms: float
    failure_type: RetrievalFailureType
    error_type: str | None
    created_at: str


def build_retrieval_trace(
    config: KnowledgeRetrievalObservabilityConfig,
    *,
    query: str,
    retrieval_mode: str,
    corpus_revision: str,
    embedding_model: str,
    embedding_revision: str,
    top_k: int,
    candidate_limit: int,
    ranked_candidates: Sequence[RankedCandidate],
    returned_chunk_ids: Sequence[str],
    indexed_chunk_count: int,
    gate_configured: bool,
    latency_ms: float,
    round_index: int = 1,
    rewrite: str | None = None,
    error_type: str | None = None,
) -> KnowledgeRetrievalTrace:
    """Build one bounded trace; callers persist it on a best-effort basis."""

    if not config.enabled:
        raise RuntimeError("Knowledge retrieval observability is disabled")
    if round_index not in {1, 2}:
        raise ValueError("Knowledge retrieval round must be 1 or 2")
    returned = frozenset(returned_chunk_ids)
    candidate_rows = [
        {
            "chunk_id": chunk_id,
            "rank": rank,
            "rrf_score": rrf_score,
            "sparse_rank": sparse_rank,
            "sparse_score": sparse_score,
            "dense_rank": dense_rank,
            "dense_score": dense_score,
            "selected": chunk_id in returned,
        }
        for rank, (
            chunk_id,
            rrf_score,
            sparse_rank,
            sparse_score,
            dense_rank,
            dense_score,
        ) in enumerate(ranked_candidates[: config.max_candidates], start=1)
    ]
    if error_type is not None:
        failure_type: RetrievalFailureType = "system"
    elif indexed_chunk_count == 0:
        failure_type = "ingestion"
    elif not ranked_candidates:
        failure_type = "retrieval"
    elif gate_configured and not returned:
        failure_type = "gate_rejected"
    else:
        failure_type = "none"
    gate_decision: GateDecision
    if not gate_configured:
        gate_decision = "not_configured"
    elif error_type is not None or not ranked_candidates:
        gate_decision = "not_evaluated"
    elif returned:
        gate_decision = "accepted"
    else:
        gate_decision = "rejected"
    return KnowledgeRetrievalTrace(
        run_id="krun_" + uuid.uuid4().hex,
        query_hash=config.fingerprint(query),
        query_length=len(query),
        round_index=round_index,
        rewrite_hash=config.fingerprint(rewrite) if rewrite is not None else None,
        retrieval_mode=retrieval_mode,
        corpus_revision=corpus_revision,
        embedding_model=embedding_model,
        embedding_revision=embedding_revision,
        top_k=top_k,
        candidate_limit=candidate_limit,
        candidate_count=len(ranked_candidates),
        returned_count=len(returned_chunk_ids),
        candidates_json=json.dumps(
            candidate_rows,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        result_coverage=min(len(returned_chunk_ids) / top_k, 1.0),
        gate_decision=gate_decision,
        latency_ms=max(latency_ms, 0.0),
        failure_type=failure_type,
        error_type=error_type,
        created_at=datetime.now(UTC).isoformat(),
    )


__all__ = [
    "KnowledgeRetrievalObservabilityConfig",
    "KnowledgeRetrievalTrace",
    "RankedCandidate",
    "RetrievalFailureType",
    "build_retrieval_trace",
]
