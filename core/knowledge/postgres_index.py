"""PostgreSQL GIN + pgvector exact projection for canonical Knowledge revisions."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from importlib import import_module
from threading import RLock
from typing import Any

from core.knowledge.parsing import MarkdownParser, ParseRequest, deserialize_document
from core.knowledge.relevance import KnowledgeRelevancePolicy
from core.knowledge.retrieval import (
    DenseEmbeddingProvider,
    HashingEmbeddingProvider,
    KnowledgeChunk,
    KnowledgeIndexSummary,
    KnowledgeRetrievalMode,
    KnowledgeSearchHit,
    chunk_document,
    citation_id,
    embedding_text,
    fts_query,
    index_text,
    reciprocal_rank_fusion,
)

POSTGRES_INDEX_SCHEMA_REVISION = "20260727_rag_postgres_exact_v1"

_POSTGRES_INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_index_schema_migrations (
    revision TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS knowledge_index_documents (
    workspace_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    page_path TEXT NOT NULL,
    title TEXT NOT NULL,
    current_revision TEXT NOT NULL,
    visibility TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (workspace_id, page_id)
);

CREATE TABLE IF NOT EXISTS knowledge_index_source_revisions (
    workspace_id TEXT NOT NULL,
    page_revision TEXT NOT NULL,
    page_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_relative_path TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    artifact_id TEXT,
    status TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_revision TEXT NOT NULL,
    embedding_dimensions INTEGER NOT NULL,
    error TEXT,
    indexed_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (workspace_id, page_revision),
    FOREIGN KEY (workspace_id, page_id)
        REFERENCES knowledge_index_documents (workspace_id, page_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_index_chunks (
    workspace_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    page_id TEXT NOT NULL,
    page_revision TEXT NOT NULL,
    page_path TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_relative_path TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    artifact_id TEXT,
    block_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    title TEXT NOT NULL,
    heading_path JSONB NOT NULL,
    page_number INTEGER,
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    visibility TEXT NOT NULL,
    language TEXT NOT NULL,
    active BOOLEAN NOT NULL,
    search_text TEXT NOT NULL,
    search_tsv TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('simple'::regconfig, search_text)
    ) STORED,
    embedding VECTOR NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_revision TEXT NOT NULL,
    embedding_dimensions INTEGER NOT NULL,
    embedding_input_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (workspace_id, chunk_id),
    UNIQUE (workspace_id, page_revision, ordinal),
    FOREIGN KEY (workspace_id, page_revision)
        REFERENCES knowledge_index_source_revisions (workspace_id, page_revision)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_index_citations (
    workspace_id TEXT NOT NULL,
    citation_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    page_revision TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (workspace_id, citation_id),
    FOREIGN KEY (workspace_id, chunk_id)
        REFERENCES knowledge_index_chunks (workspace_id, chunk_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS knowledge_retrieval_runs (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    retrieval_mode TEXT NOT NULL,
    corpus_revision TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_revision TEXT NOT NULL,
    top_k INTEGER NOT NULL,
    candidate_count INTEGER NOT NULL,
    latency_ms DOUBLE PRECISION NOT NULL,
    failure_layer TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (workspace_id, run_id)
);

CREATE INDEX IF NOT EXISTS knowledge_index_chunks_search_gin_idx
    ON knowledge_index_chunks USING GIN (search_tsv);
CREATE INDEX IF NOT EXISTS knowledge_index_chunks_scope_idx
    ON knowledge_index_chunks (
        workspace_id, visibility, active, embedding_model, embedding_revision, page_id
    );
CREATE INDEX IF NOT EXISTS knowledge_index_chunks_revision_idx
    ON knowledge_index_chunks (workspace_id, page_revision, ordinal);
CREATE INDEX IF NOT EXISTS knowledge_index_chunks_source_idx
    ON knowledge_index_chunks (workspace_id, source_id, active);
CREATE INDEX IF NOT EXISTS knowledge_index_revisions_status_idx
    ON knowledge_index_source_revisions (
        workspace_id, status, embedding_model, embedding_revision, indexed_at
    );
CREATE INDEX IF NOT EXISTS knowledge_retrieval_runs_created_idx
    ON knowledge_retrieval_runs (workspace_id, created_at DESC);
"""


@dataclass(frozen=True, slots=True)
class PostgresKnowledgeIndexConfig:
    dsn: str = field(repr=False)
    connect_timeout_seconds: int = 5
    pool_max_connections: int = 4

    def __post_init__(self) -> None:
        normalized = self.dsn.strip()
        if not normalized.startswith(("postgresql://", "postgres://")):
            raise ValueError("Knowledge PostgreSQL DSN must use postgresql://")
        if not 1 <= self.connect_timeout_seconds <= 30:
            raise ValueError(
                "Knowledge PostgreSQL connect timeout must be between 1 and 30 seconds"
            )
        if not 1 <= self.pool_max_connections <= 16:
            raise ValueError("Knowledge PostgreSQL pool size must be between 1 and 16")
        object.__setattr__(self, "dsn", normalized)


class PostgresKnowledgeIndex:
    """Rebuildable PostgreSQL search projection backed by exact pgvector scans."""

    def __init__(
        self,
        config: PostgresKnowledgeIndexConfig,
        *,
        workspace_id: str = "knowledge-local",
        embedding_provider: DenseEmbeddingProvider | None = None,
        relevance_policy: KnowledgeRelevancePolicy | None = None,
    ) -> None:
        if not workspace_id.strip() or len(workspace_id) > 128:
            raise ValueError("invalid Knowledge PostgreSQL workspace id")
        self.config = config
        self.workspace_id = workspace_id.strip()
        self.embedding_provider = embedding_provider or HashingEmbeddingProvider()
        if relevance_policy is not None:
            relevance_policy.assert_provider(
                model_id=self.embedding_provider.model_id,
                model_revision=self.embedding_provider.model_revision,
            )
        self.relevance_policy = relevance_policy
        self._markdown_parser = MarkdownParser()
        self._pool: Any | None = None
        self._pool_lock = RLock()
        self._psycopg2_extras: Any = import_module("psycopg2.extras")
        self._psycopg2_pool: Any = import_module("psycopg2.pool")
        self._register_vector: Any = import_module("pgvector.psycopg2").register_vector
        self._numpy: Any = import_module("numpy")

    @property
    def backend_id(self) -> str:
        dense = "semantic" if self.embedding_provider.supports_semantic_recall else "hashing"
        return f"postgres-tsvector+pgvector-exact+{dense}"

    def ensure_schema(self, connection: sqlite3.Connection) -> None:
        del connection
        self.ensure_postgres_schema()

    def ensure_postgres_schema(self) -> None:
        """Create the derived-index schema without requiring canonical SQLite state."""

        with self._connection(register_vector_type=False) as postgres:
            with postgres.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            postgres.commit()
            self._register_vector(postgres)
            with postgres.cursor() as cursor:
                cursor.execute(_POSTGRES_INDEX_SCHEMA)
                cursor.execute(
                    """
                    ALTER TABLE knowledge_index_source_revisions
                    ADD COLUMN IF NOT EXISTS embedding_dimensions INTEGER NOT NULL DEFAULT 0
                    """
                )
                cursor.execute(
                    """
                    INSERT INTO knowledge_index_schema_migrations (revision)
                    VALUES (%s) ON CONFLICT (revision) DO NOTHING
                    """,
                    (POSTGRES_INDEX_SCHEMA_REVISION,),
                )
            postgres.commit()

    def backfill(self, connection: sqlite3.Connection, *, force: bool = False) -> None:
        if force:
            self.delete_workspace()
        ready = set() if force else self._ready_revisions()
        rows = connection.execute(
            """
            SELECT revision_id FROM knowledge_page_revisions
            ORDER BY created_at, revision_id
            """
        ).fetchall()
        for row in rows:
            revision_id = str(row["revision_id"])
            if revision_id not in ready:
                self.sync_revision_safely(connection, revision_id)

    def sync_revision_safely(self, connection: sqlite3.Connection, revision_id: str) -> bool:
        try:
            self.sync_revision(connection, revision_id)
            return True
        except Exception as exc:
            with suppress(Exception):
                self.mark_error(
                    connection,
                    revision_id,
                    f"{type(exc).__name__}: Knowledge PostgreSQL revision projection failed",
                )
            return False

    def sync_revision(self, connection: sqlite3.Connection, revision_id: str) -> int:
        row, chunks, current_revision, created_at = self._revision_chunks(connection, revision_id)
        prepare = getattr(self.embedding_provider, "prepare", None)
        if callable(prepare):
            prepare(tuple(embedding_text(chunk) for chunk in chunks))
        prepared: list[tuple[KnowledgeChunk, tuple[float, ...], str]] = []
        for chunk in chunks:
            value = embedding_text(chunk)
            vector = tuple(float(item) for item in self.embedding_provider.embed(value))
            if len(vector) != self.embedding_provider.dimensions or any(
                not math.isfinite(item) for item in vector
            ):
                raise ValueError("invalid Knowledge PostgreSQL embedding vector")
            prepared.append(
                (
                    chunk,
                    vector,
                    "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest(),
                )
            )

        with self._connection() as postgres, postgres.cursor() as cursor:
            self._upsert_document(cursor, row, current_revision, created_at)
            if revision_id == current_revision:
                cursor.execute(
                    """
                    UPDATE knowledge_index_chunks SET active=FALSE
                    WHERE workspace_id=%s AND page_id=%s
                    """,
                    (self.workspace_id, str(row["page_id"])),
                )
            cursor.execute(
                "DELETE FROM knowledge_index_chunks WHERE workspace_id=%s AND page_revision=%s",
                (self.workspace_id, revision_id),
            )
            cursor.execute(
                """
                INSERT INTO knowledge_index_source_revisions (
                    workspace_id, page_revision, page_id, source_id, source_revision,
                    source_kind, source_relative_path, proposal_id, artifact_id,
                    status, chunk_count, embedding_model, embedding_revision,
                    embedding_dimensions, error, indexed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'ready', %s, %s, %s, %s, NULL, %s
                )
                ON CONFLICT (workspace_id, page_revision) DO UPDATE SET
                    page_id=EXCLUDED.page_id,
                    source_id=EXCLUDED.source_id,
                    source_revision=EXCLUDED.source_revision,
                    source_kind=EXCLUDED.source_kind,
                    source_relative_path=EXCLUDED.source_relative_path,
                    proposal_id=EXCLUDED.proposal_id,
                    artifact_id=EXCLUDED.artifact_id,
                    status='ready',
                    chunk_count=EXCLUDED.chunk_count,
                    embedding_model=EXCLUDED.embedding_model,
                    embedding_revision=EXCLUDED.embedding_revision,
                    embedding_dimensions=EXCLUDED.embedding_dimensions,
                    error=NULL,
                    indexed_at=EXCLUDED.indexed_at
                """,
                (
                    self.workspace_id,
                    revision_id,
                    str(row["page_id"]),
                    str(row["source_id"]),
                    str(row["source_revision"]),
                    str(row["source_kind"]),
                    str(row["source_relative_path"]),
                    str(row["proposal_id"]),
                    str(row["parse_artifact_id"]) if row["parse_artifact_id"] else None,
                    len(chunks),
                    self.embedding_provider.model_id,
                    self.embedding_provider.model_revision,
                    self.embedding_provider.dimensions,
                    created_at,
                ),
            )
            for chunk, vector, input_hash in prepared:
                self._insert_chunk(cursor, chunk, vector, input_hash, created_at)
            postgres.commit()
        return len(chunks)

    def mark_error(
        self,
        connection: sqlite3.Connection,
        revision_id: str,
        message: str,
    ) -> None:
        row, _chunks, current_revision, created_at = self._revision_chunks(connection, revision_id)
        with self._connection() as postgres, postgres.cursor() as cursor:
            self._upsert_document(cursor, row, current_revision, created_at)
            cursor.execute(
                "DELETE FROM knowledge_index_chunks WHERE workspace_id=%s AND page_revision=%s",
                (self.workspace_id, revision_id),
            )
            cursor.execute(
                """
                INSERT INTO knowledge_index_source_revisions (
                    workspace_id, page_revision, page_id, source_id, source_revision,
                    source_kind, source_relative_path, proposal_id, artifact_id,
                    status, chunk_count, embedding_model, embedding_revision,
                    embedding_dimensions, error, indexed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'error', 0, %s, %s, %s, %s, CURRENT_TIMESTAMP
                )
                ON CONFLICT (workspace_id, page_revision) DO UPDATE SET
                    status='error', chunk_count=0,
                    embedding_model=EXCLUDED.embedding_model,
                    embedding_revision=EXCLUDED.embedding_revision,
                    embedding_dimensions=EXCLUDED.embedding_dimensions,
                    error=EXCLUDED.error,
                    indexed_at=EXCLUDED.indexed_at
                """,
                (
                    self.workspace_id,
                    revision_id,
                    str(row["page_id"]),
                    str(row["source_id"]),
                    str(row["source_revision"]),
                    str(row["source_kind"]),
                    str(row["source_relative_path"]),
                    str(row["proposal_id"]),
                    str(row["parse_artifact_id"]) if row["parse_artifact_id"] else None,
                    self.embedding_provider.model_id,
                    self.embedding_provider.model_revision,
                    self.embedding_provider.dimensions,
                    message[:500],
                ),
            )
            postgres.commit()

    def summary(self, connection: sqlite3.Connection) -> KnowledgeIndexSummary:
        revision_count = int(
            connection.execute("SELECT COUNT(*) FROM knowledge_page_revisions").fetchone()[0]
        )
        with self._connection() as postgres, postgres.cursor() as cursor:
            cursor.execute(
                """
                    SELECT
                        COUNT(*) FILTER (WHERE status='ready'),
                        COUNT(*) FILTER (WHERE status='error')
                    FROM knowledge_index_source_revisions
                    WHERE workspace_id=%s AND embedding_model=%s AND embedding_revision=%s
                      AND embedding_dimensions=%s
                    """,
                (
                    self.workspace_id,
                    self.embedding_provider.model_id,
                    self.embedding_provider.model_revision,
                    self.embedding_provider.dimensions,
                ),
            )
            indexed_revision_count, error_count = cursor.fetchone()
            cursor.execute(
                """
                    SELECT COUNT(*) FILTER (WHERE active), COUNT(*)
                    FROM knowledge_index_chunks
                    WHERE workspace_id=%s AND embedding_model=%s AND embedding_revision=%s
                      AND embedding_dimensions=%s
                    """,
                (
                    self.workspace_id,
                    self.embedding_provider.model_id,
                    self.embedding_provider.model_revision,
                    self.embedding_provider.dimensions,
                ),
            )
            active_chunk_count, total_chunk_count = cursor.fetchone()
        corpus_revision = self.corpus_revision(connection)
        policy_compatible = (
            self.relevance_policy is not None
            and self.relevance_policy.corpus_revision == corpus_revision
        )
        return KnowledgeIndexSummary(
            backend=self.backend_id,
            embedding_model=self.embedding_provider.model_id,
            embedding_revision=self.embedding_provider.model_revision,
            corpus_revision=corpus_revision,
            relevance_policy_id=(
                self.relevance_policy.policy_id if self.relevance_policy is not None else None
            ),
            abstention_enabled=policy_compatible,
            revision_count=revision_count,
            indexed_revision_count=int(indexed_revision_count or 0),
            active_chunk_count=int(active_chunk_count or 0),
            total_chunk_count=int(total_chunk_count or 0),
            error_count=int(error_count or 0),
        )

    def search(
        self,
        connection: sqlite3.Connection,
        query: str,
        *,
        top_k: int = 8,
        visibility: str = "private",
        source_ids: tuple[str, ...] = (),
        page_revisions: tuple[str, ...] = (),
        retrieval_mode: KnowledgeRetrievalMode = "hybrid",
    ) -> tuple[KnowledgeSearchHit, ...]:
        self._validate_search(
            connection,
            query,
            top_k=top_k,
            visibility=visibility,
            source_ids=source_ids,
            page_revisions=page_revisions,
            retrieval_mode=retrieval_mode,
        )
        normalized = query.strip()
        candidate_limit = min(200, max(20, top_k * 5))
        where, filter_params = self._filters(
            visibility=visibility,
            source_ids=source_ids,
            page_revisions=page_revisions,
        )
        sparse_rows: tuple[dict[str, Any], ...] = ()
        dense_rows: tuple[dict[str, Any], ...] = ()
        with self._connection() as postgres:
            if retrieval_mode in {"sparse", "hybrid"}:
                with postgres.cursor(cursor_factory=self._psycopg2_extras.RealDictCursor) as cursor:
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
                        WHERE chunk.search_tsv @@ query.value AND {where}
                        ORDER BY score DESC, chunk.source_relative_path,
                                 chunk.source_revision, chunk.ordinal,
                                 chunk.content_hash, chunk.chunk_id
                        LIMIT %s
                        """,
                        (fts_query(normalized), *filter_params, candidate_limit),
                    )
                    sparse_rows = tuple(cursor.fetchall())
            if retrieval_mode in {"dense", "hybrid"}:
                query_vector = tuple(
                    float(item) for item in self.embedding_provider.embed(normalized)
                )
                if len(query_vector) != self.embedding_provider.dimensions:
                    raise ValueError("Knowledge PostgreSQL query embedding dimensions do not match")
                vector = self._database_vector(query_vector)
                with postgres.cursor(cursor_factory=self._psycopg2_extras.RealDictCursor) as cursor:
                    cursor.execute(
                        f"""
                        WITH query AS (SELECT %s::vector AS value)
                        SELECT chunk.chunk_id,
                               1 - (chunk.embedding <=> query.value) AS score,
                               chunk.source_relative_path, chunk.source_revision,
                               chunk.ordinal, chunk.content_hash
                        FROM knowledge_index_chunks AS chunk
                        CROSS JOIN query
                        WHERE {where}
                          AND chunk.embedding_dimensions=%s
                        ORDER BY chunk.embedding <=> query.value,
                                 chunk.source_relative_path, chunk.source_revision,
                                 chunk.ordinal, chunk.content_hash, chunk.chunk_id
                        LIMIT %s
                        """,
                        (
                            vector,
                            *filter_params,
                            self.embedding_provider.dimensions,
                            candidate_limit,
                        ),
                    )
                    dense_rows = tuple(cursor.fetchall())

        sparse = [(str(row["chunk_id"]), float(row["score"])) for row in sparse_rows]
        dense = [
            (str(row["chunk_id"]), float(row["score"]))
            for row in dense_rows
            if row["score"] is not None and float(row["score"]) > 0.0
        ]
        if retrieval_mode == "hybrid" and not self.embedding_provider.supports_semantic_recall:
            sparse_ids = {chunk_id for chunk_id, _score in sparse}
            dense = [item for item in dense if item[0] in sparse_ids]
        stable_rows = (*sparse_rows, *dense_rows)
        tie_breakers = {
            str(row["chunk_id"]): "\0".join(
                (
                    str(row["source_relative_path"]),
                    str(row["source_revision"]),
                    f"{int(row['ordinal']):08d}",
                    str(row["content_hash"]),
                )
            )
            for row in stable_rows
        }
        fused = reciprocal_rank_fusion(sparse, dense, tie_breakers=tie_breakers)[:top_k]
        if self.relevance_policy is not None:
            fused = [
                item
                for item in fused
                if self.relevance_policy.accepts(
                    sparse_score=item[3],
                    dense_score=item[5],
                    hybrid_score=item[1],
                    retrieval_mode=retrieval_mode,
                )
            ]
        chunk_ids = [item[0] for item in fused]
        if not chunk_ids:
            return ()
        chunks = self._chunks_by_id(chunk_ids)
        return tuple(
            KnowledgeSearchHit(
                chunk=chunks[chunk_id],
                citation_id=citation_id(chunks[chunk_id]),
                rank=rank,
                rrf_score=rrf_score,
                sparse_rank=sparse_rank,
                sparse_score=sparse_score,
                dense_rank=dense_rank,
                dense_score=dense_score,
                retrieval_route=retrieval_mode,
            )
            for rank, (
                chunk_id,
                rrf_score,
                sparse_rank,
                sparse_score,
                dense_rank,
                dense_score,
            ) in enumerate(fused, start=1)
            if chunk_id in chunks
        )

    def corpus_revision(self, connection: sqlite3.Connection) -> str:
        del connection
        with self._connection() as postgres, postgres.cursor() as cursor:
            cursor.execute(
                """
                    SELECT source_relative_path, source_revision, ordinal, content_hash
                    FROM knowledge_index_chunks
                    WHERE workspace_id=%s AND active=TRUE
                      AND embedding_model=%s AND embedding_revision=%s
                      AND embedding_dimensions=%s
                    ORDER BY source_relative_path, source_revision, ordinal, content_hash
                    """,
                (
                    self.workspace_id,
                    self.embedding_provider.model_id,
                    self.embedding_provider.model_revision,
                    self.embedding_provider.dimensions,
                ),
            )
            rows = cursor.fetchall()
        payload = "\n".join(f"{row[0]}\0{row[1]}\0{row[2]}\0{row[3]}" for row in rows)
        return "kcorpus_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def resolve_citations(
        self,
        connection: sqlite3.Connection,
        citation_ids: tuple[str, ...],
        *,
        visibility: str = "private",
    ) -> tuple[tuple[str, KnowledgeChunk], ...]:
        del connection
        if not citation_ids or len(citation_ids) > 8:
            raise ValueError("knowledge learning requires between 1 and 8 citations")
        if len(set(citation_ids)) != len(citation_ids):
            raise ValueError("knowledge learning citations must be unique")
        if visibility not in {"private", "public"}:
            raise ValueError("invalid knowledge visibility")
        with (
            self._connection() as postgres,
            postgres.cursor(cursor_factory=self._psycopg2_extras.RealDictCursor) as cursor,
        ):
            cursor.execute(
                """
                    SELECT chunk.* FROM knowledge_index_citations AS citation
                    JOIN knowledge_index_chunks AS chunk
                      ON chunk.workspace_id=citation.workspace_id
                     AND chunk.chunk_id=citation.chunk_id
                    WHERE citation.workspace_id=%s
                      AND citation.citation_id=ANY(%s)
                      AND chunk.visibility=%s AND chunk.active=TRUE
                      AND chunk.embedding_model=%s AND chunk.embedding_revision=%s
                      AND chunk.embedding_dimensions=%s
                    """,
                (
                    self.workspace_id,
                    list(citation_ids),
                    visibility,
                    self.embedding_provider.model_id,
                    self.embedding_provider.model_revision,
                    self.embedding_provider.dimensions,
                ),
            )
            chunks = {citation_id(self._chunk(row)): self._chunk(row) for row in cursor.fetchall()}
        missing = [item for item in citation_ids if item not in chunks]
        if missing:
            raise KeyError("knowledge learning citation is stale or unknown")
        return tuple((item, chunks[item]) for item in citation_ids)

    def representative_chunks(
        self,
        connection: sqlite3.Connection,
        page_revisions: tuple[str, ...],
        *,
        visibility: str = "private",
        source_ids: tuple[str, ...] = (),
        per_page: int = 1,
    ) -> tuple[KnowledgeChunk, ...]:
        del connection
        if not page_revisions or len(page_revisions) > 50:
            raise ValueError("knowledge relation targets must contain 1 to 50 revisions")
        if per_page < 1 or per_page > 3:
            raise ValueError("knowledge relation chunks per page must be between 1 and 3")
        where, params = self._filters(
            visibility=visibility,
            source_ids=source_ids,
            page_revisions=page_revisions,
        )
        with (
            self._connection() as postgres,
            postgres.cursor(cursor_factory=self._psycopg2_extras.RealDictCursor) as cursor,
        ):
            cursor.execute(
                f"""
                    SELECT chunk.* FROM knowledge_index_chunks AS chunk
                    WHERE {where}
                    ORDER BY chunk.page_revision, chunk.ordinal, chunk.chunk_id
                    """,
                params,
            )
            rows = cursor.fetchall()
        counts: dict[str, int] = {}
        selected: list[KnowledgeChunk] = []
        for row in rows:
            revision = str(row["page_revision"])
            if counts.get(revision, 0) >= per_page:
                continue
            selected.append(self._chunk(row))
            counts[revision] = counts.get(revision, 0) + 1
        return tuple(selected)

    def storage_summary(self) -> dict[str, int]:
        with self._connection() as postgres, postgres.cursor() as cursor:
            cursor.execute(
                """
                    SELECT COALESCE(SUM(pg_column_size(chunk)), 0)
                    FROM knowledge_index_chunks AS chunk WHERE workspace_id=%s
                    """,
                (self.workspace_id,),
            )
            workspace_row_bytes = int(cursor.fetchone()[0])
            cursor.execute(
                """
                    SELECT
                        pg_total_relation_size('knowledge_index_chunks'),
                        pg_relation_size('knowledge_index_chunks_search_gin_idx')
                    """
            )
            relation_total_bytes, gin_index_bytes = cursor.fetchone()
        return {
            "workspace_row_bytes": workspace_row_bytes,
            "shared_chunks_relation_total_bytes": int(relation_total_bytes),
            "shared_gin_index_bytes": int(gin_index_bytes),
        }

    def delete_workspace(self) -> None:
        with self._connection() as postgres, postgres.cursor() as cursor:
            cursor.execute(
                "DELETE FROM knowledge_retrieval_runs WHERE workspace_id=%s",
                (self.workspace_id,),
            )
            cursor.execute(
                "DELETE FROM knowledge_index_documents WHERE workspace_id=%s",
                (self.workspace_id,),
            )
            postgres.commit()

    def close(self) -> None:
        with self._pool_lock:
            if self._pool is not None:
                self._pool.closeall()
                self._pool = None

    def _ready_revisions(self) -> tuple[str, ...]:
        with self._connection() as postgres, postgres.cursor() as cursor:
            cursor.execute(
                """
                    SELECT page_revision FROM knowledge_index_source_revisions
                    WHERE workspace_id=%s AND status='ready'
                      AND embedding_model=%s AND embedding_revision=%s
                      AND embedding_dimensions=%s
                    """,
                (
                    self.workspace_id,
                    self.embedding_provider.model_id,
                    self.embedding_provider.model_revision,
                    self.embedding_provider.dimensions,
                ),
            )
            return tuple(str(row[0]) for row in cursor.fetchall())

    def _revision_chunks(
        self,
        connection: sqlite3.Connection,
        revision_id: str,
    ) -> tuple[sqlite3.Row, tuple[KnowledgeChunk, ...], str, str]:
        row = connection.execute(
            """
            SELECT revision.revision_id, revision.page_id, revision.content,
                   revision.source_revision, revision.proposal_id, revision.created_at,
                   page.path AS page_path, page.current_revision,
                   proposal.source_id, proposal.source_kind,
                   proposal.source_relative_path, proposal.title,
                   proposal.parse_artifact_id,
                   artifact.payload_json AS artifact_payload
            FROM knowledge_page_revisions AS revision
            JOIN knowledge_pages AS page ON page.page_id=revision.page_id
            JOIN knowledge_proposals AS proposal ON proposal.proposal_id=revision.proposal_id
            LEFT JOIN knowledge_parse_artifacts AS artifact
              ON artifact.artifact_id=proposal.parse_artifact_id
            WHERE revision.revision_id=?
            """,
            (revision_id,),
        ).fetchone()
        if row is None:
            raise KeyError(revision_id)
        artifact_id = str(row["parse_artifact_id"]) if row["parse_artifact_id"] else None
        if artifact_id is not None:
            if row["artifact_payload"] is None:
                raise ValueError("knowledge parse artifact is missing")
            document = deserialize_document(str(row["artifact_payload"]))
            if document.source_revision != str(row["source_revision"]):
                raise ValueError("knowledge parse artifact revision mismatch")
        else:
            document = self._markdown_parser.parse(
                ParseRequest(
                    source_id=str(row["source_id"]),
                    relative_path=str(row["page_path"]),
                    source_revision=revision_id,
                    media_type="text/markdown",
                    payload=str(row["content"]).encode("utf-8"),
                )
            )
        current_revision = str(row["current_revision"])
        chunks = chunk_document(
            document,
            workspace_id=self.workspace_id,
            page_id=str(row["page_id"]),
            page_revision=revision_id,
            page_path=str(row["page_path"]),
            source_id=str(row["source_id"]),
            source_revision=str(row["source_revision"]),
            source_kind=str(row["source_kind"]),
            source_relative_path=str(row["source_relative_path"]),
            proposal_id=str(row["proposal_id"]),
            artifact_id=artifact_id,
            title=str(row["title"]),
            visibility="private",
            active=revision_id == current_revision,
        )
        return row, chunks, current_revision, str(row["created_at"])

    def _upsert_document(
        self,
        cursor: Any,
        row: sqlite3.Row,
        current_revision: str,
        created_at: str,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO knowledge_index_documents (
                workspace_id, page_id, page_path, title,
                current_revision, visibility, updated_at
            ) VALUES (%s, %s, %s, %s, %s, 'private', %s)
            ON CONFLICT (workspace_id, page_id) DO UPDATE SET
                page_path=EXCLUDED.page_path,
                title=EXCLUDED.title,
                current_revision=EXCLUDED.current_revision,
                visibility=EXCLUDED.visibility,
                updated_at=EXCLUDED.updated_at
            """,
            (
                self.workspace_id,
                str(row["page_id"]),
                str(row["page_path"]),
                str(row["title"]),
                current_revision,
                created_at,
            ),
        )

    def _insert_chunk(
        self,
        cursor: Any,
        chunk: KnowledgeChunk,
        vector: tuple[float, ...],
        input_hash: str,
        created_at: str,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO knowledge_index_chunks (
                workspace_id, chunk_id, page_id, page_revision, page_path,
                source_id, source_revision, source_kind, source_relative_path,
                proposal_id, artifact_id, block_id, ordinal, title, heading_path,
                page_number, text, token_count, content_hash, visibility, language,
                active, search_text, embedding, embedding_model, embedding_revision,
                embedding_dimensions, embedding_input_hash, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                self.workspace_id,
                chunk.chunk_id,
                chunk.page_id,
                chunk.page_revision,
                chunk.page_path,
                chunk.source_id,
                chunk.source_revision,
                chunk.source_kind,
                chunk.source_relative_path,
                chunk.proposal_id,
                chunk.artifact_id,
                chunk.block_id,
                chunk.ordinal,
                chunk.title,
                json.dumps(chunk.heading_path, ensure_ascii=False),
                chunk.page_number,
                chunk.text,
                chunk.token_count,
                chunk.content_hash,
                chunk.visibility,
                chunk.language,
                chunk.active,
                index_text(chunk),
                self._database_vector(vector),
                self.embedding_provider.model_id,
                self.embedding_provider.model_revision,
                self.embedding_provider.dimensions,
                input_hash,
                created_at,
            ),
        )
        cursor.execute(
            """
            INSERT INTO knowledge_index_citations (
                workspace_id, citation_id, chunk_id, page_revision,
                source_revision, content_hash, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                self.workspace_id,
                citation_id(chunk),
                chunk.chunk_id,
                chunk.page_revision,
                chunk.source_revision,
                chunk.content_hash,
                created_at,
            ),
        )

    def _validate_search(
        self,
        connection: sqlite3.Connection,
        query: str,
        *,
        top_k: int,
        visibility: str,
        source_ids: tuple[str, ...],
        page_revisions: tuple[str, ...],
        retrieval_mode: KnowledgeRetrievalMode,
    ) -> None:
        if top_k < 1 or top_k > 50:
            raise ValueError("knowledge search top_k must be between 1 and 50")
        if self.relevance_policy is not None and top_k > self.relevance_policy.top_k:
            raise ValueError("knowledge search top_k exceeds calibrated relevance policy")
        if self.relevance_policy is not None:
            if retrieval_mode != "hybrid":
                raise ValueError("calibrated relevance policy only supports hybrid retrieval")
            self.relevance_policy.assert_corpus(corpus_revision=self.corpus_revision(connection))
        if len(source_ids) > 100 or len(page_revisions) > 100:
            raise ValueError("knowledge search filters are too large")
        if visibility not in {"private", "public"}:
            raise ValueError("invalid knowledge visibility")
        if retrieval_mode not in {"sparse", "dense", "hybrid"}:
            raise ValueError("invalid knowledge retrieval mode")
        normalized = query.strip()
        if not normalized or len(normalized) > 2_000:
            raise ValueError("knowledge query must be between 1 and 2000 characters")

    def _filters(
        self,
        *,
        visibility: str,
        source_ids: tuple[str, ...],
        page_revisions: tuple[str, ...],
    ) -> tuple[str, tuple[object, ...]]:
        clauses = [
            "chunk.workspace_id=%s",
            "chunk.visibility=%s",
            "chunk.embedding_model=%s",
            "chunk.embedding_revision=%s",
            "chunk.embedding_dimensions=%s",
        ]
        params: list[object] = [
            self.workspace_id,
            visibility,
            self.embedding_provider.model_id,
            self.embedding_provider.model_revision,
            self.embedding_provider.dimensions,
        ]
        if source_ids:
            clauses.append("chunk.source_id=ANY(%s)")
            params.append(list(source_ids))
        if page_revisions:
            clauses.append("chunk.page_revision=ANY(%s)")
            params.append(list(page_revisions))
        else:
            clauses.append("chunk.active=TRUE")
        return " AND ".join(clauses), tuple(params)

    def _chunks_by_id(self, chunk_ids: list[str]) -> dict[str, KnowledgeChunk]:
        with (
            self._connection() as postgres,
            postgres.cursor(cursor_factory=self._psycopg2_extras.RealDictCursor) as cursor,
        ):
            cursor.execute(
                """
                    SELECT * FROM knowledge_index_chunks
                    WHERE workspace_id=%s AND chunk_id=ANY(%s)
                    """,
                (self.workspace_id, chunk_ids),
            )
            rows = cursor.fetchall()
        return {str(row["chunk_id"]): self._chunk(row) for row in rows}

    @staticmethod
    def _chunk(row: dict[str, Any]) -> KnowledgeChunk:
        heading_path = row["heading_path"]
        if not isinstance(heading_path, list):
            raise ValueError("invalid Knowledge PostgreSQL chunk heading path")
        return KnowledgeChunk(
            chunk_id=str(row["chunk_id"]),
            workspace_id=str(row["workspace_id"]),
            page_id=str(row["page_id"]),
            page_revision=str(row["page_revision"]),
            page_path=str(row["page_path"]),
            source_id=str(row["source_id"]),
            source_revision=str(row["source_revision"]),
            source_kind=str(row["source_kind"]),
            source_relative_path=str(row["source_relative_path"]),
            proposal_id=str(row["proposal_id"]),
            artifact_id=str(row["artifact_id"]) if row["artifact_id"] else None,
            block_id=str(row["block_id"]),
            ordinal=int(row["ordinal"]),
            title=str(row["title"]),
            heading_path=tuple(str(item) for item in heading_path),
            page_number=int(row["page_number"]) if row["page_number"] is not None else None,
            text=str(row["text"]),
            token_count=int(row["token_count"]),
            content_hash=str(row["content_hash"]),
            visibility=str(row["visibility"]),
            language=str(row["language"]),
            active=bool(row["active"]),
        )

    @contextmanager
    def _connection(self, *, register_vector_type: bool = True) -> Iterator[Any]:
        pool = self._get_pool()
        connection = pool.getconn()
        try:
            if register_vector_type:
                self._register_vector(connection)
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            pool.putconn(connection)

    def _get_pool(self) -> Any:
        with self._pool_lock:
            if self._pool is None:
                self._pool = self._psycopg2_pool.ThreadedConnectionPool(
                    1,
                    self.config.pool_max_connections,
                    dsn=self.config.dsn,
                    connect_timeout=self.config.connect_timeout_seconds,
                    application_name="sage-knowledge-index",
                )
            return self._pool

    def _database_vector(self, vector: tuple[float, ...]) -> Any:
        # pgvector-python 0.3.x registers NumPy arrays for Psycopg2 adaptation.
        return self._numpy.asarray(vector, dtype=self._numpy.float32)
