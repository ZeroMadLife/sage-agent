from __future__ import annotations

from typing import Any

import pytest

from core.knowledge.postgres_retrieval import (
    FallbackSparseRetriever,
    NativePostgresFtsRetriever,
    PgTextsearchBm25Retriever,
    PgvectorHnswRetriever,
    ReciprocalRankFusionPolicy,
    build_sparse_retriever,
)


class RecordingCursor:
    def __init__(self, rows: tuple[dict[str, Any], ...]) -> None:
        self.rows = rows
        self.sql = ""
        self.statements: list[str] = []
        self.params: tuple[object, ...] = ()

    def execute(self, sql: str, params: tuple[object, ...] = ()) -> None:
        self.sql = sql
        self.statements.append(sql)
        self.params = params

    def fetchall(self) -> tuple[dict[str, Any], ...]:
        return self.rows

    def fetchone(self) -> tuple[bool]:
        return (False,)


def test_native_sparse_retriever_owns_postgres_fts_sql() -> None:
    cursor = RecordingCursor(({"chunk_id": "chunk-1", "score": 0.5},))
    retriever = NativePostgresFtsRetriever()

    rows = retriever.search(
        cursor,
        query="分工提高生产力",
        where_sql="chunk.workspace_id=%s",
        filter_params=("workspace",),
        candidate_limit=20,
    )

    assert rows == cursor.rows
    assert retriever.backend_id == "postgres-tsvector"
    assert "websearch_to_tsquery" in cursor.sql
    assert "ts_rank_cd" in cursor.sql
    assert cursor.params[-2:] == ("workspace", 20)


def test_hnsw_retriever_uses_halfvec_for_doubao_2048_dimensions() -> None:
    cursor = RecordingCursor(({"chunk_id": "chunk-1", "score": 0.5},))
    retriever = PgvectorHnswRetriever(dimensions=2_048, ef_search=80)

    rows = retriever.search(
        cursor,
        query_vector="[0.1,0.2]",
        dimensions=2_048,
        where_sql="chunk.workspace_id=%s",
        filter_params=("workspace",),
        candidate_limit=20,
    )

    assert rows == cursor.rows
    assert retriever.backend_id == "pgvector-hnsw-halfvec-ef80"
    assert "halfvec(2048)" in cursor.sql
    assert "halfvec_cosine_ops" not in cursor.sql
    assert "hnsw.ef_search" in cursor.statements[0]
    assert cursor.params[-3:] == ("workspace", 2_048, 20)


def test_pg_textsearch_retriever_uses_explicit_bm25_index_and_positive_score() -> None:
    cursor = RecordingCursor(({"chunk_id": "chunk-1", "score": 2.0},))
    retriever = PgTextsearchBm25Retriever(index_name="knowledge_chunks_bm25_eval_idx")

    rows = retriever.search(
        cursor,
        query="分工提高生产力",
        where_sql="chunk.workspace_id=%s",
        filter_params=("workspace",),
        candidate_limit=20,
    )

    assert rows == cursor.rows
    assert retriever.backend_id == "pg-textsearch-bm25"
    assert "to_bm25query" in cursor.sql
    assert "-(chunk.search_text <@> query.value) AS score" in cursor.sql
    assert cursor.params[1:] == ("knowledge_chunks_bm25_eval_idx", "workspace", 20)
    assert "分工" in str(cursor.params[0])


def test_pg_textsearch_retriever_bounds_query_terms() -> None:
    cursor = RecordingCursor(())
    retriever = PgTextsearchBm25Retriever(max_query_terms=8)

    retriever.search(
        cursor,
        query=" ".join(f"term-{index}" for index in range(100)),
        where_sql="TRUE",
        filter_params=(),
        candidate_limit=20,
    )

    assert len(str(cursor.params[0]).split()) <= 8


def test_pg_textsearch_retriever_probes_extension_availability() -> None:
    cursor = RecordingCursor(())
    retriever = PgTextsearchBm25Retriever()

    assert retriever.is_available(cursor) is False
    assert "pg_available_extensions" in cursor.sql
    assert cursor.params == ("pg_textsearch",)


def test_fallback_sparse_retriever_selects_native_when_bm25_is_unavailable() -> None:
    cursor = RecordingCursor(())
    retriever = FallbackSparseRetriever(
        preferred=PgTextsearchBm25Retriever(),
        fallback=NativePostgresFtsRetriever(),
    )

    retriever.ensure_schema(cursor)

    assert retriever.backend_id == "postgres-tsvector"


def test_fallback_sparse_retriever_keeps_bm25_when_extension_is_available() -> None:
    class AvailableCursor(RecordingCursor):
        def fetchone(self) -> tuple[bool]:
            return (True,)

    cursor = AvailableCursor(())
    retriever = FallbackSparseRetriever(
        preferred=PgTextsearchBm25Retriever(index_name="sage_bm25_eval_idx"),
        fallback=NativePostgresFtsRetriever(),
    )

    retriever.ensure_schema(cursor)

    assert retriever.backend_id == "pg-textsearch-bm25"
    assert any("CREATE EXTENSION IF NOT EXISTS pg_textsearch" in sql for sql in cursor.statements)


@pytest.mark.parametrize(
    ("strategy", "backend_id"),
    [("native", "postgres-tsvector"), ("bm25", "pg-textsearch-bm25")],
)
def test_build_sparse_retriever_exposes_explicit_routes(strategy: str, backend_id: str) -> None:
    assert build_sparse_retriever(strategy).backend_id == backend_id


def test_build_sparse_retriever_rejects_unknown_routes() -> None:
    with pytest.raises(ValueError, match="unknown PostgreSQL sparse strategy"):
        build_sparse_retriever("unknown")


@pytest.mark.parametrize("index_name", ["1_bm25", "bm25-index", "索引_bm25"])
def test_pg_textsearch_retriever_rejects_non_ascii_sql_identifiers(index_name: str) -> None:
    with pytest.raises(ValueError, match="SQL identifier"):
        PgTextsearchBm25Retriever(index_name=index_name)


def test_rrf_policy_preserves_route_specific_diagnostics() -> None:
    policy = ReciprocalRankFusionPolicy(rank_constant=60)

    ranked = policy.fuse(
        [("sparse-only", 3.0), ("both", 2.0)],
        [("both", 0.9), ("dense-only", 0.8)],
        tie_breakers={
            "sparse-only": "b",
            "both": "a",
            "dense-only": "c",
        },
    )

    assert policy.policy_id == "rrf-k60"
    assert ranked[0][0] == "both"
    assert ranked[0][2:] == (2, 2.0, 1, 0.9)
