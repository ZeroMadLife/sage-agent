from __future__ import annotations

import pytest

from evals.book_learning_hnsw import (
    BookHnswConfig,
    BookHnswMeasurement,
    choose_hnsw_storage,
    decide_book_hnsw_gate,
)


def test_doubao_dimensions_require_halfvec_hnsw_storage() -> None:
    assert choose_hnsw_storage(1_024).sql_type == "vector"
    assert choose_hnsw_storage(2_048).sql_type == "halfvec"
    assert choose_hnsw_storage(2_048).operator_class == "halfvec_cosine_ops"

    with pytest.raises(ValueError, match="4000"):
        choose_hnsw_storage(4_001)


def test_real_book_gate_keeps_exact_when_exact_is_inside_sla() -> None:
    config = BookHnswConfig(exact_p95_sla_ms=100.0)
    decision = decide_book_hnsw_gate(
        config,
        exact_p95_ms=80.0,
        measurements=(BookHnswMeasurement(80, 1.0, 20.0, 30.0, 42),),
    )

    assert decision.eligible_ef_search == (80,)
    assert decision.runtime_recommendation == "keep_exact"
    assert decision.runtime_index_enabled is False


def test_real_book_gate_requires_recall_sla_and_material_speedup() -> None:
    config = BookHnswConfig(exact_p95_sla_ms=100.0)
    decision = decide_book_hnsw_gate(
        config,
        exact_p95_ms=150.0,
        measurements=(
            BookHnswMeasurement(40, 0.95, 30.0, 50.0, 42),
            BookHnswMeasurement(80, 0.99, 80.0, 125.0, 42),
            BookHnswMeasurement(120, 0.99, 40.0, 70.0, 42),
        ),
    )

    assert decision.eligible_ef_search == (120,)
    assert decision.runtime_recommendation == "hnsw_eligible_for_followup"
