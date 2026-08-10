"""Decision contract for the real long-book pgvector HNSW ablation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class HnswStorage:
    sql_type: str
    dimensions: int
    operator_class: str
    lossy_cast: bool


@dataclass(frozen=True, slots=True)
class BookHnswConfig:
    exact_p95_sla_ms: float = 100.0
    minimum_recall_at_k: float = 0.98
    minimum_latency_reduction: float = 0.20
    ef_search_values: tuple[int, ...] = (40, 80, 120, 200)
    hnsw_m: int = 16
    hnsw_ef_construction: int = 64

    def __post_init__(self) -> None:
        if self.exact_p95_sla_ms <= 0:
            raise ValueError("exact P95 SLA must be positive")
        if not 0 < self.minimum_recall_at_k <= 1:
            raise ValueError("minimum HNSW recall must be in (0, 1]")
        if not 0 <= self.minimum_latency_reduction < 1:
            raise ValueError("minimum HNSW latency reduction must be in [0, 1)")
        if self.ef_search_values != tuple(sorted(set(self.ef_search_values))):
            raise ValueError("ef_search values must be unique and increasing")


@dataclass(frozen=True, slots=True)
class BookHnswMeasurement:
    ef_search: int
    recall_at_k: float
    p50_ms: float
    p95_ms: float
    measured_queries: int


@dataclass(frozen=True, slots=True)
class BookHnswDecision:
    exact_sla_triggered: bool
    eligible_ef_search: tuple[int, ...]
    runtime_recommendation: str
    runtime_index_enabled: bool = False


def choose_hnsw_storage(dimensions: int) -> HnswStorage:
    """Select the pgvector HNSW representation for a fixed embedding dimension."""

    if not 1 <= dimensions <= 4_000:
        raise ValueError("pgvector HNSW supports at most 4000 halfvec dimensions")
    if dimensions <= 2_000:
        return HnswStorage("vector", dimensions, "vector_cosine_ops", False)
    return HnswStorage("halfvec", dimensions, "halfvec_cosine_ops", True)


def decide_book_hnsw_gate(
    config: BookHnswConfig,
    *,
    exact_p95_ms: float,
    measurements: tuple[BookHnswMeasurement, ...],
) -> BookHnswDecision:
    """Require recall, SLA and speedup before HNSW can enter a follow-up."""

    if exact_p95_ms <= 0:
        raise ValueError("exact P95 must be positive")
    maximum_hnsw_p95 = exact_p95_ms * (1.0 - config.minimum_latency_reduction)
    eligible = tuple(
        item.ef_search
        for item in measurements
        if item.recall_at_k >= config.minimum_recall_at_k
        and item.p95_ms <= config.exact_p95_sla_ms
        and item.p95_ms <= maximum_hnsw_p95
    )
    triggered = exact_p95_ms > config.exact_p95_sla_ms
    return BookHnswDecision(
        exact_sla_triggered=triggered,
        eligible_ef_search=eligible,
        runtime_recommendation=(
            "hnsw_eligible_for_followup"
            if triggered and eligible
            else ("keep_exact_hnsw_not_eligible" if triggered else "keep_exact")
        ),
    )


__all__ = [
    "BookHnswConfig",
    "BookHnswDecision",
    "BookHnswMeasurement",
    "HnswStorage",
    "choose_hnsw_storage",
    "decide_book_hnsw_gate",
]
