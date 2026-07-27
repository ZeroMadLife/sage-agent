from __future__ import annotations

import os

import psycopg2
import pytest

from core.knowledge.scale_benchmark import ScaleBenchmarkConfig, run_scale_benchmark

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_dsn() -> str:
    value = os.environ.get("SAGE_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("SAGE_TEST_POSTGRES_DSN is required for PostgreSQL integration tests")
    return value


def test_postgres_scale_benchmark_keeps_exact_and_cleans_ephemeral_table(
    postgres_dsn: str,
) -> None:
    config = ScaleBenchmarkConfig(
        scales=(128, 512),
        dimensions=64,
        top_k=3,
        query_count=8,
        warmup_passes=0,
        measured_passes=1,
        exact_p95_sla_ms=60_000.0,
        ef_search_values=(20, 40),
    )

    report = run_scale_benchmark(postgres_dsn, config=config)

    assert [item["scale"] for item in report["exact"]] == [128, 512]
    assert all(item["recall_at_10"] == 1.0 for item in report["exact"])
    assert report["decision"]["runtime_recommendation"] == "keep_exact"
    assert report["hnsw"] == []
    _assert_no_ephemeral_tables(postgres_dsn)


def test_postgres_scale_benchmark_runs_conditional_hnsw_curve(
    postgres_dsn: str,
) -> None:
    config = ScaleBenchmarkConfig(
        scales=(512,),
        dimensions=64,
        top_k=3,
        query_count=8,
        warmup_passes=0,
        measured_passes=1,
        exact_p95_sla_ms=0.000001,
        ef_search_values=(20, 40),
    )

    report = run_scale_benchmark(postgres_dsn, config=config)

    assert report["decision"]["hnsw_experiment_required"] is True
    assert [item["ef_search"] for item in report["hnsw"]] == [20, 40]
    assert all(item["recall_at_10"] >= 0.0 for item in report["hnsw"])
    assert "Index Scan" in report["hnsw"][0]["explain"]["plan_node_types"]
    _assert_no_ephemeral_tables(postgres_dsn)


def _assert_no_ephemeral_tables(postgres_dsn: str) -> None:
    with psycopg2.connect(postgres_dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*) FROM pg_tables
            WHERE schemaname=current_schema()
              AND tablename LIKE 'sage_hnsw_scale_bench_%'
            """
        )
        assert cursor.fetchone() == (0,)
