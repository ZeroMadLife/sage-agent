"""Replaceable PostgreSQL candidate retrieval and rank-fusion strategies."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias

from core.knowledge.retrieval import fts_query, lexical_terms, reciprocal_rank_fusion

PostgresCandidateRow: TypeAlias = dict[str, Any]
RankedCandidateInput: TypeAlias = list[tuple[str, float]]
FusedCandidate: TypeAlias = tuple[
    str,
    float,
    int | None,
    float | None,
    int | None,
    float | None,
]


class SparseCandidateRetriever(Protocol):
    """Return bounded lexical candidates without owning fusion or answer policy."""

    @property
    def backend_id(self) -> str: ...

    def ensure_schema(self, cursor: Any) -> None: ...

    def search(
        self,
        cursor: Any,
        *,
        query: str,
        where_sql: str,
        filter_params: tuple[object, ...],
        candidate_limit: int,
    ) -> tuple[PostgresCandidateRow, ...]: ...


class DenseCandidateRetriever(Protocol):
    """Return bounded vector candidates without owning embedding generation."""

    @property
    def backend_id(self) -> str: ...

    def search(
        self,
        cursor: Any,
        *,
        query_vector: object,
        dimensions: int,
        where_sql: str,
        filter_params: tuple[object, ...],
        candidate_limit: int,
    ) -> tuple[PostgresCandidateRow, ...]: ...


class RankFusionPolicy(Protocol):
    """Fuse route-specific ranks while preserving their diagnostic scores."""

    @property
    def policy_id(self) -> str: ...

    def fuse(
        self,
        sparse: RankedCandidateInput,
        dense: RankedCandidateInput,
        *,
        tie_breakers: Mapping[str, str],
    ) -> list[FusedCandidate]: ...


@dataclass(frozen=True, slots=True)
class NativePostgresFtsRetriever:
    """PostgreSQL GIN candidate retrieval ranked by cover density."""

    @property
    def backend_id(self) -> str:
        return "postgres-tsvector"

    def ensure_schema(self, cursor: Any) -> None:
        del cursor

    def search(
        self,
        cursor: Any,
        *,
        query: str,
        where_sql: str,
        filter_params: tuple[object, ...],
        candidate_limit: int,
    ) -> tuple[PostgresCandidateRow, ...]:
        cursor.execute(
            f"""
            WITH query AS (
                SELECT websearch_to_tsquery('simple'::regconfig, %s) AS value
            )
            SELECT chunk.chunk_id,
                   ts_rank_cd(chunk.search_tsv, query.value, 33) AS score,
                   chunk.source_relative_path, chunk.source_revision,
                   chunk.ordinal, chunk.content_hash
            FROM knowledge_index_chunks AS chunk
            CROSS JOIN query
            WHERE chunk.search_tsv @@ query.value AND {where_sql}
            ORDER BY score DESC, chunk.source_relative_path,
                     chunk.source_revision, chunk.ordinal,
                     chunk.content_hash, chunk.chunk_id
            LIMIT %s
            """,
            (fts_query(query), *filter_params, candidate_limit),
        )
        return tuple(cursor.fetchall())


@dataclass(frozen=True, slots=True)
class PgTextsearchBm25Retriever:
    """Optional pg_textsearch BM25 route for an isolated PostgreSQL 17+ database."""

    index_name: str = "knowledge_index_chunks_search_bm25_idx"
    max_query_terms: int = 32

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.index_name) is None:
            raise ValueError("pg_textsearch BM25 index name must be an SQL identifier")
        if not 1 <= self.max_query_terms <= 64:
            raise ValueError("pg_textsearch BM25 query term limit must be between 1 and 64")

    @property
    def backend_id(self) -> str:
        return "pg-textsearch-bm25"

    def ensure_schema(self, cursor: Any) -> None:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_textsearch")
        cursor.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {self.index_name}
            ON knowledge_index_chunks USING bm25 (search_text)
            WITH (text_config='simple')
            """
        )

    def search(
        self,
        cursor: Any,
        *,
        query: str,
        where_sql: str,
        filter_params: tuple[object, ...],
        candidate_limit: int,
    ) -> tuple[PostgresCandidateRow, ...]:
        terms = " ".join(dict.fromkeys(lexical_terms(query, limit=self.max_query_terms)))
        if not terms:
            raise ValueError("knowledge query has no searchable terms")
        cursor.execute(
            f"""
            WITH query AS (
                SELECT to_bm25query(%s, %s) AS value
            )
            SELECT chunk.chunk_id,
                   -(chunk.search_text <@> query.value) AS score,
                   chunk.source_relative_path, chunk.source_revision,
                   chunk.ordinal, chunk.content_hash
            FROM knowledge_index_chunks AS chunk
            CROSS JOIN query
            WHERE {where_sql}
            ORDER BY chunk.search_text <@> query.value,
                     chunk.source_relative_path, chunk.source_revision,
                     chunk.ordinal, chunk.content_hash, chunk.chunk_id
            LIMIT %s
            """,
            (terms, self.index_name, *filter_params, candidate_limit),
        )
        return tuple(cursor.fetchall())


@dataclass(frozen=True, slots=True)
class PgvectorExactRetriever:
    """Exact cosine candidate retrieval over pgvector."""

    @property
    def backend_id(self) -> str:
        return "pgvector-exact"

    def search(
        self,
        cursor: Any,
        *,
        query_vector: object,
        dimensions: int,
        where_sql: str,
        filter_params: tuple[object, ...],
        candidate_limit: int,
    ) -> tuple[PostgresCandidateRow, ...]:
        cursor.execute(
            f"""
            WITH query AS (SELECT %s::vector AS value)
            SELECT chunk.chunk_id,
                   1 - (chunk.embedding <=> query.value) AS score,
                   chunk.source_relative_path, chunk.source_revision,
                   chunk.ordinal, chunk.content_hash
            FROM knowledge_index_chunks AS chunk
            CROSS JOIN query
            WHERE {where_sql}
              AND chunk.embedding_dimensions=%s
            ORDER BY chunk.embedding <=> query.value,
                     chunk.source_relative_path, chunk.source_revision,
                     chunk.ordinal, chunk.content_hash, chunk.chunk_id
            LIMIT %s
            """,
            (
                query_vector,
                *filter_params,
                dimensions,
                candidate_limit,
            ),
        )
        return tuple(cursor.fetchall())


@dataclass(frozen=True, slots=True)
class ReciprocalRankFusionPolicy:
    """Application-layer RRF shared by native FTS and extension-backed sparse routes."""

    rank_constant: int = 60

    def __post_init__(self) -> None:
        if self.rank_constant < 1:
            raise ValueError("RRF rank constant must be positive")

    @property
    def policy_id(self) -> str:
        return f"rrf-k{self.rank_constant}"

    def fuse(
        self,
        sparse: RankedCandidateInput,
        dense: RankedCandidateInput,
        *,
        tie_breakers: Mapping[str, str],
    ) -> list[FusedCandidate]:
        return reciprocal_rank_fusion(
            sparse,
            dense,
            rank_constant=self.rank_constant,
            tie_breakers=tie_breakers,
        )


__all__ = [
    "DenseCandidateRetriever",
    "NativePostgresFtsRetriever",
    "PgTextsearchBm25Retriever",
    "PgvectorExactRetriever",
    "RankFusionPolicy",
    "ReciprocalRankFusionPolicy",
    "SparseCandidateRetriever",
]
