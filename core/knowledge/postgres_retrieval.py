"""Replaceable PostgreSQL candidate retrieval and rank-fusion strategies."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeAlias, cast

from core.knowledge.retrieval import fts_query, lexical_terms, reciprocal_rank_fusion

PostgresCandidateRow: TypeAlias = dict[str, Any]
PostgresSparseStrategy: TypeAlias = Literal["native", "auto", "bm25"]
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


@dataclass(slots=True)
class FallbackSparseRetriever:
    """Prefer an extension-backed sparse route and fall back to native FTS."""

    preferred: SparseCandidateRetriever
    fallback: SparseCandidateRetriever
    _active: SparseCandidateRetriever | None = field(default=None, init=False, repr=False)

    @property
    def backend_id(self) -> str:
        active = self._active
        if active is not None:
            return active.backend_id
        return f"auto({self.preferred.backend_id}|fallback={self.fallback.backend_id})"

    def ensure_schema(self, cursor: Any) -> None:
        availability_probe = getattr(self.preferred, "is_available", None)
        if callable(availability_probe) and not availability_probe(cursor):
            self._active = self.fallback
        else:
            self._active = self.preferred
        self._active.ensure_schema(cursor)

    def search(
        self,
        cursor: Any,
        *,
        query: str,
        where_sql: str,
        filter_params: tuple[object, ...],
        candidate_limit: int,
    ) -> tuple[PostgresCandidateRow, ...]:
        active = self._active
        if active is None:
            raise RuntimeError("sparse retriever schema must be initialized before search")
        return active.search(
            cursor,
            query=query,
            where_sql=where_sql,
            filter_params=filter_params,
            candidate_limit=candidate_limit,
        )


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

    def is_available(self, cursor: Any) -> bool:
        """Check extension availability without mutating the current transaction."""

        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM pg_available_extensions WHERE name=%s
            )
            """,
            ("pg_textsearch",),
        )
        row = cursor.fetchone()
        return bool(row and row[0])

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


def build_sparse_retriever(strategy: PostgresSparseStrategy) -> SparseCandidateRetriever:
    """Build an explicit sparse route without coupling callers to implementations."""

    if strategy == "native":
        return NativePostgresFtsRetriever()
    if strategy == "bm25":
        return PgTextsearchBm25Retriever()
    if strategy == "auto":
        return FallbackSparseRetriever(
            preferred=PgTextsearchBm25Retriever(),
            fallback=NativePostgresFtsRetriever(),
        )
    raise ValueError("unknown PostgreSQL sparse strategy")


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
class PgvectorHnswRetriever:
    """Opt-in pgvector HNSW route, including the halfvec path for 2048-D vectors.

    The class only owns candidate SQL and session tuning. Index creation remains
    an evaluation/deployment concern so the exact default cannot be changed by
    constructing a retriever accidentally.
    """

    dimensions: int
    ef_search: int = 80

    def __post_init__(self) -> None:
        if not 1 <= self.dimensions <= 4_000:
            raise ValueError("HNSW dimensions must be between 1 and 4000")
        if not 1 <= self.ef_search <= 10_000:
            raise ValueError("HNSW ef_search must be between 1 and 10000")

    @property
    def backend_id(self) -> str:
        storage = "vector" if self.dimensions <= 2_000 else "halfvec"
        return f"pgvector-hnsw-{storage}-ef{self.ef_search}"

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
        if dimensions != self.dimensions:
            raise ValueError("HNSW query dimensions do not match retriever dimensions")
        cast_type = "vector" if dimensions <= 2_000 else f"halfvec({dimensions})"
        embedding = (
            "chunk.embedding" if dimensions <= 2_000 else f"chunk.embedding::halfvec({dimensions})"
        )
        cursor.execute("SET LOCAL hnsw.ef_search = %s", (self.ef_search,))
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute(
            f"""
            WITH query AS (SELECT (%s::vector)::{cast_type} AS value)
            SELECT chunk.chunk_id,
                   1 - ({embedding} <=> query.value) AS score,
                   chunk.source_relative_path, chunk.source_revision,
                   chunk.ordinal, chunk.content_hash
            FROM knowledge_index_chunks AS chunk
            CROSS JOIN query
            WHERE {where_sql}
              AND chunk.embedding_dimensions=%s
            ORDER BY {embedding} <=> query.value,
                     chunk.source_relative_path, chunk.source_revision,
                     chunk.ordinal, chunk.content_hash, chunk.chunk_id
            LIMIT %s
            """,
            (query_vector, *filter_params, dimensions, candidate_limit),
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
        return cast(
            list[FusedCandidate],
            reciprocal_rank_fusion(
                sparse,
                dense,
                rank_constant=self.rank_constant,
                tie_breakers=tie_breakers,
            ),
        )


__all__ = [
    "DenseCandidateRetriever",
    "FallbackSparseRetriever",
    "NativePostgresFtsRetriever",
    "PgTextsearchBm25Retriever",
    "PgvectorExactRetriever",
    "PgvectorHnswRetriever",
    "PostgresSparseStrategy",
    "RankFusionPolicy",
    "ReciprocalRankFusionPolicy",
    "SparseCandidateRetriever",
    "build_sparse_retriever",
]
