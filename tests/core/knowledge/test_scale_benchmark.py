from __future__ import annotations

import math

import pytest

from core.knowledge.scale_benchmark import (
    DEFAULT_SCALE_BENCHMARK_CONFIG,
    HnswMeasurement,
    ScaleBenchmarkConfig,
    ScaleMeasurement,
    decide_hnsw_gate,
    distractor_vector,
    gold_chunk_ids,
    percentile,
    positive_vector,
    query_vector,
)


def test_default_scale_contract_is_frozen() -> None:
    config = DEFAULT_SCALE_BENCHMARK_CONFIG

    assert config.scales == (1_000, 10_000, 100_000)
    assert config.dimensions == 384
    assert config.top_k == 10
    assert config.query_count == 32
    assert config.warmup_passes == 1
    assert config.measured_passes == 2
    assert config.exact_p95_sla_ms == 100.0
    assert config.ef_search_values == (40, 80, 120, 200)


def test_scale_contract_rejects_ambiguous_or_unsafe_values() -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        ScaleBenchmarkConfig(scales=(1_000, 1_000))
    with pytest.raises(ValueError, match="reserved dimensions"):
        ScaleBenchmarkConfig(dimensions=40, query_count=32, top_k=10)
    with pytest.raises(ValueError, match="measured passes"):
        ScaleBenchmarkConfig(measured_passes=0)
    with pytest.raises(ValueError, match="ef_search"):
        ScaleBenchmarkConfig(ef_search_values=(40, 40))


def test_synthetic_vectors_have_stable_gold_and_disjoint_distractors() -> None:
    config = ScaleBenchmarkConfig(
        scales=(128,),
        dimensions=64,
        top_k=3,
        query_count=8,
        warmup_passes=0,
        measured_passes=1,
        ef_search_values=(20, 40),
    )
    query = query_vector(2, config)
    positives = [positive_vector(2, rank, config) for rank in range(config.top_k)]
    distractor = distractor_vector(123, config)

    positive_distances = [
        1.0 - sum(a * b for a, b in zip(query, item, strict=True)) for item in positives
    ]
    assert positive_distances == sorted(positive_distances)
    assert all(distance > 0.0 for distance in positive_distances)
    assert math.isclose(
        sum(a * b for a, b in zip(query, distractor, strict=True)),
        0.0,
        abs_tol=1e-12,
    )
    assert gold_chunk_ids(2, config) == (
        "gold-q002-r000",
        "gold-q002-r001",
        "gold-q002-r002",
    )


def test_percentile_uses_linear_interpolation() -> None:
    assert percentile((1.0, 2.0, 3.0, 4.0), 0.5) == 2.5
    assert percentile((1.0, 2.0, 3.0, 4.0), 0.95) == pytest.approx(3.85)
    with pytest.raises(ValueError, match="at least one"):
        percentile((), 0.95)


def test_gate_keeps_exact_when_sla_is_not_triggered() -> None:
    config = ScaleBenchmarkConfig(scales=(1_000,), measured_passes=1)
    exact = (_scale_measurement(1_000, p95_ms=99.9),)

    decision = decide_hnsw_gate(config=config, exact=exact, hnsw=())

    assert decision.triggered_scales == ()
    assert decision.hnsw_experiment_required is False
    assert decision.runtime_recommendation == "keep_exact"
    assert decision.eligible_ef_search == ()


def test_gate_requires_recall_latency_and_material_speedup() -> None:
    config = ScaleBenchmarkConfig(scales=(100_000,), measured_passes=1)
    exact = (_scale_measurement(100_000, p95_ms=150.0),)
    hnsw = (
        _hnsw_measurement(40, recall=0.97, p95_ms=70.0),
        _hnsw_measurement(80, recall=0.99, p95_ms=125.0),
        _hnsw_measurement(120, recall=0.99, p95_ms=90.0),
    )

    decision = decide_hnsw_gate(config=config, exact=exact, hnsw=hnsw)

    assert decision.triggered_scales == (100_000,)
    assert decision.hnsw_experiment_required is True
    assert decision.eligible_ef_search == (120,)
    assert decision.runtime_recommendation == "hnsw_eligible_for_followup"


def _scale_measurement(scale: int, *, p95_ms: float) -> ScaleMeasurement:
    return ScaleMeasurement(
        scale=scale,
        recall_at_10=1.0,
        p50_ms=p95_ms * 0.8,
        p95_ms=p95_ms,
        measured_queries=64,
        data_load_ms=1.0,
        scalar_index_build_ms=1.0,
        analyze_ms=1.0,
        table_bytes=1,
        total_index_bytes=1,
        total_relation_bytes=2,
        explain={},
        backend_memory={},
    )


def _hnsw_measurement(
    ef_search: int,
    *,
    recall: float,
    p95_ms: float,
) -> HnswMeasurement:
    return HnswMeasurement(
        ef_search=ef_search,
        recall_at_10=recall,
        p50_ms=p95_ms * 0.8,
        p95_ms=p95_ms,
        measured_queries=64,
        explain={},
        backend_memory={},
    )
