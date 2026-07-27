"""Rebuildable SQLite FTS5 and dense index for the local knowledge workspace."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import replace

from core.knowledge.observability import (
    KnowledgeRetrievalObservabilityConfig,
    KnowledgeRetrievalTrace,
    RankedCandidate,
    build_retrieval_trace,
)
from core.knowledge.parsing import MarkdownParser, ParseRequest, deserialize_document
from core.knowledge.relevance import KnowledgeRelevancePolicy
from core.knowledge.retrieval import (
    DenseEmbeddingProvider,
    HashingEmbeddingProvider,
    KnowledgeAblationPolicy,
    KnowledgeChunk,
    KnowledgeIndexSummary,
    KnowledgeRetrievalMode,
    KnowledgeSearchHit,
    chunk_document,
    citation_id,
    cosine_similarity,
    deserialize_vector,
    embedding_text,
    fts_query,
    index_text,
    reciprocal_rank_fusion,
    serialize_vector,
)

_INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    chunk_id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
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
    heading_path_json TEXT NOT NULL,
    page_number INTEGER,
    block_kind TEXT NOT NULL DEFAULT 'paragraph',
    bbox_json TEXT,
    media_ref TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    parser_id TEXT NOT NULL DEFAULT '',
    parser_version TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    visibility TEXT NOT NULL,
    language TEXT NOT NULL,
    active INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(page_revision, ordinal)
);
CREATE INDEX IF NOT EXISTS knowledge_chunks_active_idx
    ON knowledge_chunks(workspace_id, visibility, active, page_id);
CREATE INDEX IF NOT EXISTS knowledge_chunks_revision_idx
    ON knowledge_chunks(page_revision, ordinal);
CREATE TABLE IF NOT EXISTS knowledge_chunk_embeddings (
    chunk_id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    model_revision TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    input_hash TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_chunk_embeddings_model_idx
    ON knowledge_chunk_embeddings(model_id, model_revision);
CREATE TABLE IF NOT EXISTS knowledge_index_revisions (
    page_revision TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    chunk_count INTEGER NOT NULL,
    embedding_model TEXT NOT NULL DEFAULT '',
    embedding_revision TEXT NOT NULL DEFAULT '',
    error TEXT,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_index_revisions_status_idx
    ON knowledge_index_revisions(status, indexed_at);
CREATE TABLE IF NOT EXISTS knowledge_retrieval_runs (
    workspace_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    query_hash TEXT NOT NULL,
    query_length INTEGER NOT NULL,
    round_index INTEGER NOT NULL,
    rewrite_hash TEXT,
    retrieval_mode TEXT NOT NULL,
    corpus_revision TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_revision TEXT NOT NULL,
    top_k INTEGER NOT NULL,
    candidate_limit INTEGER NOT NULL,
    candidate_count INTEGER NOT NULL,
    returned_count INTEGER NOT NULL,
    candidates_json TEXT NOT NULL,
    result_coverage REAL NOT NULL,
    gate_decision TEXT NOT NULL,
    latency_ms REAL NOT NULL,
    failure_type TEXT NOT NULL,
    error_type TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, run_id)
);
CREATE INDEX IF NOT EXISTS knowledge_retrieval_runs_created_idx
    ON knowledge_retrieval_runs(workspace_id, created_at DESC);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
    chunk_id UNINDEXED,
    terms,
    tokenize='unicode61 remove_diacritics 2'
);
"""

logger = logging.getLogger(__name__)


class LocalKnowledgeIndex:
    """Local backend matching the future PostgreSQL FTS + pgvector contract."""

    def __init__(
        self,
        *,
        workspace_id: str = "knowledge-local",
        embedding_provider: DenseEmbeddingProvider | None = None,
        relevance_policy: KnowledgeRelevancePolicy | None = None,
        observability: KnowledgeRetrievalObservabilityConfig | None = None,
        ablation_policy: KnowledgeAblationPolicy | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.embedding_provider = embedding_provider or HashingEmbeddingProvider()
        if relevance_policy is not None:
            relevance_policy.assert_provider(
                model_id=self.embedding_provider.model_id,
                model_revision=self.embedding_provider.model_revision,
            )
        self.relevance_policy = relevance_policy
        self.observability = observability or KnowledgeRetrievalObservabilityConfig()
        self.ablation_policy = ablation_policy or KnowledgeAblationPolicy()
        self._markdown_parser = MarkdownParser()

    @property
    def backend_id(self) -> str:
        dense = "semantic" if self.embedding_provider.supports_semantic_recall else "hashing"
        return f"sqlite-fts5+{dense}"

    def ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(_INDEX_SCHEMA)
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(knowledge_index_revisions)").fetchall()
        }
        if "embedding_model" not in columns:
            connection.execute(
                "ALTER TABLE knowledge_index_revisions "
                "ADD COLUMN embedding_model TEXT NOT NULL DEFAULT ''"
            )
        if "embedding_revision" not in columns:
            connection.execute(
                "ALTER TABLE knowledge_index_revisions "
                "ADD COLUMN embedding_revision TEXT NOT NULL DEFAULT ''"
            )
        chunk_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(knowledge_chunks)").fetchall()
        }
        additions = {
            "block_kind": "TEXT NOT NULL DEFAULT 'paragraph'",
            "bbox_json": "TEXT",
            "media_ref": "TEXT",
            "confidence": "REAL NOT NULL DEFAULT 1.0",
            "parser_id": "TEXT NOT NULL DEFAULT ''",
            "parser_version": "TEXT NOT NULL DEFAULT ''",
        }
        for name, definition in additions.items():
            if name not in chunk_columns:
                connection.execute(f"ALTER TABLE knowledge_chunks ADD COLUMN {name} {definition}")

    def backfill(self, connection: sqlite3.Connection, *, force: bool = False) -> None:
        if force:
            connection.execute("DELETE FROM knowledge_chunks_fts")
            connection.execute("DELETE FROM knowledge_chunk_embeddings")
            connection.execute("DELETE FROM knowledge_chunks")
            connection.execute("DELETE FROM knowledge_index_revisions")
        rows = connection.execute(
            """
            SELECT revision_id FROM knowledge_page_revisions AS revision
            WHERE ? OR NOT EXISTS (
                SELECT 1 FROM knowledge_index_revisions AS indexed
                WHERE indexed.page_revision=revision.revision_id
                  AND indexed.status='ready'
                  AND indexed.embedding_model=?
                  AND indexed.embedding_revision=?
            )
            ORDER BY created_at, revision_id
            """,
            (
                int(force),
                self.embedding_provider.model_id,
                self.embedding_provider.model_revision,
            ),
        ).fetchall()
        for row in rows:
            self.sync_revision_safely(connection, str(row["revision_id"]))

    def sync_revision_safely(self, connection: sqlite3.Connection, revision_id: str) -> bool:
        """Keep a failed derived projection from leaving partial chunks behind."""

        connection.execute("SAVEPOINT knowledge_index_revision")
        try:
            self.sync_revision(connection, revision_id)
            connection.execute("RELEASE SAVEPOINT knowledge_index_revision")
            return True
        except Exception:
            connection.execute("ROLLBACK TO SAVEPOINT knowledge_index_revision")
            connection.execute("RELEASE SAVEPOINT knowledge_index_revision")
            self.mark_error(connection, revision_id, "knowledge revision indexing failed")
            return False

    def sync_revision(self, connection: sqlite3.Connection, revision_id: str) -> int:
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
            JOIN knowledge_pages AS page ON page.page_id = revision.page_id
            JOIN knowledge_proposals AS proposal
              ON proposal.proposal_id = revision.proposal_id
            LEFT JOIN knowledge_parse_artifacts AS artifact
              ON artifact.artifact_id = proposal.parse_artifact_id
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
            content = str(row["content"])
            document = self._markdown_parser.parse(
                ParseRequest(
                    source_id=str(row["source_id"]),
                    relative_path=str(row["page_path"]),
                    source_revision=revision_id,
                    media_type="text/markdown",
                    payload=content.encode("utf-8"),
                )
            )
        is_active = revision_id == str(row["current_revision"])
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
            active=is_active,
            ablation_policy=self.ablation_policy,
            semantic_provider=self.embedding_provider,
        )
        prepare = getattr(self.embedding_provider, "prepare", None)
        if callable(prepare):
            prepare(
                tuple(
                    embedding_text(chunk, ablation_policy=self.ablation_policy) for chunk in chunks
                )
            )
        old_ids = [
            str(item["chunk_id"])
            for item in connection.execute(
                "SELECT chunk_id FROM knowledge_chunks WHERE page_revision=?",
                (revision_id,),
            ).fetchall()
        ]
        if old_ids:
            connection.executemany(
                "DELETE FROM knowledge_chunks_fts WHERE chunk_id=?",
                ((chunk_id,) for chunk_id in old_ids),
            )
            connection.executemany(
                "DELETE FROM knowledge_chunk_embeddings WHERE chunk_id=?",
                ((chunk_id,) for chunk_id in old_ids),
            )
            connection.execute("DELETE FROM knowledge_chunks WHERE page_revision=?", (revision_id,))
        if is_active:
            connection.execute(
                "UPDATE knowledge_chunks SET active=0 WHERE page_id=?",
                (str(row["page_id"]),),
            )
        for chunk in chunks:
            self._insert_chunk(connection, replace(chunk, active=is_active), str(row["created_at"]))
        connection.execute(
            """
            INSERT INTO knowledge_index_revisions (
                page_revision, status, chunk_count, embedding_model,
                embedding_revision, error, indexed_at
            ) VALUES (?, 'ready', ?, ?, ?, NULL, ?)
            ON CONFLICT(page_revision) DO UPDATE SET
                status='ready', chunk_count=excluded.chunk_count,
                embedding_model=excluded.embedding_model,
                embedding_revision=excluded.embedding_revision,
                error=NULL, indexed_at=excluded.indexed_at
            """,
            (
                revision_id,
                len(chunks),
                self.embedding_provider.model_id,
                self.embedding_provider.model_revision,
                str(row["created_at"]),
            ),
        )
        return len(chunks)

    def mark_error(self, connection: sqlite3.Connection, revision_id: str, message: str) -> None:
        connection.execute(
            """
            INSERT INTO knowledge_index_revisions (
                page_revision, status, chunk_count, embedding_model,
                embedding_revision, error, indexed_at
            ) VALUES (?, 'error', 0, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(page_revision) DO UPDATE SET
                status='error', chunk_count=0, error=excluded.error,
                embedding_model=excluded.embedding_model,
                embedding_revision=excluded.embedding_revision,
                indexed_at=excluded.indexed_at
            """,
            (
                revision_id,
                self.embedding_provider.model_id,
                self.embedding_provider.model_revision,
                message[:500],
            ),
        )

    def summary(self, connection: sqlite3.Connection) -> KnowledgeIndexSummary:
        revision_count = int(
            connection.execute("SELECT COUNT(*) FROM knowledge_page_revisions").fetchone()[0]
        )
        indexed_revision_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_index_revisions "
                "WHERE status='ready' AND embedding_model=? AND embedding_revision=?",
                (
                    self.embedding_provider.model_id,
                    self.embedding_provider.model_revision,
                ),
            ).fetchone()[0]
        )
        active_chunk_count = int(
            connection.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE active=1").fetchone()[0]
        )
        total_chunk_count = int(
            connection.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
        )
        error_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_index_revisions "
                "WHERE status='error' AND embedding_model=? AND embedding_revision=?",
                (
                    self.embedding_provider.model_id,
                    self.embedding_provider.model_revision,
                ),
            ).fetchone()[0]
        )
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
            indexed_revision_count=indexed_revision_count,
            active_chunk_count=active_chunk_count,
            total_chunk_count=total_chunk_count,
            error_count=error_count,
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
        round_index: int = 1,
        trace_query: str | None = None,
        rewrite: str | None = None,
    ) -> tuple[KnowledgeSearchHit, ...]:
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
        trace_source = normalized if trace_query is None else trace_query.strip()
        if not trace_source or len(trace_source) > 2_000:
            raise ValueError("knowledge trace query must be between 1 and 2000 characters")
        if not normalized or len(normalized) > 2_000:
            raise ValueError("knowledge query must be between 1 and 2000 characters")
        candidate_limit = min(200, max(20, top_k * 5))
        started = time.perf_counter()
        where, filter_params = self._filters(
            visibility=visibility,
            source_ids=source_ids,
            page_revisions=page_revisions,
        )
        sparse_rows = (
            connection.execute(
                f"""
                SELECT fts.chunk_id, bm25(knowledge_chunks_fts) AS score,
                       chunk.source_relative_path, chunk.source_revision,
                       chunk.ordinal, chunk.content_hash
                FROM knowledge_chunks_fts AS fts
                JOIN knowledge_chunks AS chunk ON chunk.chunk_id = fts.chunk_id
                WHERE knowledge_chunks_fts MATCH ? AND {where}
                ORDER BY score, chunk.source_relative_path, chunk.source_revision,
                         chunk.ordinal, chunk.content_hash, fts.chunk_id
                LIMIT ?
                """,
                (fts_query(normalized), *filter_params, candidate_limit),
            ).fetchall()
            if retrieval_mode in {"sparse", "hybrid"}
            else ()
        )
        sparse = [(str(row["chunk_id"]), -float(row["score"])) for row in sparse_rows]
        try:
            query_vector = (
                self.embedding_provider.embed(normalized)
                if retrieval_mode in {"dense", "hybrid"}
                else ()
            )
        except Exception as exc:
            self._record_retrieval_trace(
                connection,
                query=trace_source,
                retrieval_mode=retrieval_mode,
                top_k=top_k,
                candidate_limit=candidate_limit,
                ranked_candidates=[],
                returned_chunk_ids=(),
                latency_ms=(time.perf_counter() - started) * 1_000,
                error_type=type(exc).__name__,
                round_index=round_index,
                rewrite=rewrite,
            )
            raise
        dense_rows = (
            connection.execute(
                f"""
                SELECT embedding.chunk_id, embedding.dimensions, embedding.vector_json,
                       chunk.source_relative_path, chunk.source_revision,
                       chunk.ordinal, chunk.content_hash
                FROM knowledge_chunk_embeddings AS embedding
                JOIN knowledge_chunks AS chunk ON chunk.chunk_id = embedding.chunk_id
                WHERE embedding.model_id=? AND embedding.model_revision=? AND {where}
                """,
                (
                    self.embedding_provider.model_id,
                    self.embedding_provider.model_revision,
                    *filter_params,
                ),
            ).fetchall()
            if retrieval_mode in {"dense", "hybrid"}
            else ()
        )
        dense_ranked = sorted(
            (
                (
                    str(row["chunk_id"]),
                    cosine_similarity(
                        query_vector,
                        deserialize_vector(
                            str(row["vector_json"]),
                            dimensions=int(row["dimensions"]),
                        ),
                    ),
                    (
                        str(row["source_relative_path"]),
                        str(row["source_revision"]),
                        int(row["ordinal"]),
                        str(row["content_hash"]),
                    ),
                )
                for row in dense_rows
            ),
            key=lambda item: (-item[1], item[2], item[0]),
        )
        dense = [(chunk_id, score) for chunk_id, score, _stable_key in dense_ranked if score > 0.0][
            :candidate_limit
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
        ranked = reciprocal_rank_fusion(sparse, dense, tie_breakers=tie_breakers)
        fused = ranked[:top_k]
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
            self._record_retrieval_trace(
                connection,
                query=trace_source,
                retrieval_mode=retrieval_mode,
                top_k=top_k,
                candidate_limit=candidate_limit,
                ranked_candidates=ranked,
                returned_chunk_ids=(),
                latency_ms=(time.perf_counter() - started) * 1_000,
                round_index=round_index,
                rewrite=rewrite,
            )
            return ()
        placeholders = ",".join("?" for _ in chunk_ids)
        chunk_rows = connection.execute(
            f"SELECT * FROM knowledge_chunks WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        chunks = {str(row["chunk_id"]): self._chunk(row) for row in chunk_rows}
        hits = tuple(
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
        self._record_retrieval_trace(
            connection,
            query=trace_source,
            retrieval_mode=retrieval_mode,
            top_k=top_k,
            candidate_limit=candidate_limit,
            ranked_candidates=ranked,
            returned_chunk_ids=tuple(hit.chunk.chunk_id for hit in hits),
            latency_ms=(time.perf_counter() - started) * 1_000,
            round_index=round_index,
            rewrite=rewrite,
        )
        return hits

    def _record_retrieval_trace(
        self,
        connection: sqlite3.Connection,
        *,
        query: str,
        retrieval_mode: KnowledgeRetrievalMode,
        top_k: int,
        candidate_limit: int,
        ranked_candidates: list[RankedCandidate],
        returned_chunk_ids: tuple[str, ...],
        latency_ms: float,
        error_type: str | None = None,
        round_index: int = 1,
        rewrite: str | None = None,
    ) -> None:
        if not self.observability.enabled:
            return
        try:
            indexed_chunk_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_chunks WHERE workspace_id=? AND active=1",
                    (self.workspace_id,),
                ).fetchone()[0]
            )
            trace = build_retrieval_trace(
                self.observability,
                workspace_id=self.workspace_id,
                query=query,
                retrieval_mode=retrieval_mode,
                corpus_revision=self.corpus_revision(connection),
                embedding_model=self.embedding_provider.model_id,
                embedding_revision=self.embedding_provider.model_revision,
                top_k=top_k,
                candidate_limit=candidate_limit,
                ranked_candidates=ranked_candidates,
                returned_chunk_ids=returned_chunk_ids,
                indexed_chunk_count=indexed_chunk_count,
                gate_configured=self.relevance_policy is not None,
                latency_ms=latency_ms,
                round_index=round_index,
                rewrite=rewrite,
                error_type=error_type,
            )
            self._insert_retrieval_trace(connection, trace)
            connection.commit()
        except Exception as exc:
            logger.warning(
                "Knowledge SQLite retrieval trace persistence failed (%s)",
                type(exc).__name__,
            )
            return

    def _insert_retrieval_trace(
        self,
        connection: sqlite3.Connection,
        trace: KnowledgeRetrievalTrace,
    ) -> None:
        connection.execute(
            """
            INSERT INTO knowledge_retrieval_runs (
                workspace_id, run_id, query_hash, query_length, round_index,
                rewrite_hash, retrieval_mode, corpus_revision, embedding_model,
                embedding_revision, top_k, candidate_limit, candidate_count,
                returned_count, candidates_json, result_coverage, gate_decision,
                latency_ms, failure_type, error_type, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.workspace_id,
                trace.run_id,
                trace.query_hash,
                trace.query_length,
                trace.round_index,
                trace.rewrite_hash,
                trace.retrieval_mode,
                trace.corpus_revision,
                trace.embedding_model,
                trace.embedding_revision,
                trace.top_k,
                trace.candidate_limit,
                trace.candidate_count,
                trace.returned_count,
                trace.candidates_json,
                trace.result_coverage,
                trace.gate_decision,
                trace.latency_ms,
                trace.failure_type,
                trace.error_type,
                trace.created_at,
            ),
        )

    def corpus_revision(self, connection: sqlite3.Connection) -> str:
        rows = connection.execute(
            """
            SELECT source_relative_path, source_revision, ordinal, content_hash
            FROM knowledge_chunks
            WHERE workspace_id=? AND active=1
            ORDER BY source_relative_path, source_revision, ordinal, content_hash
            """,
            (self.workspace_id,),
        ).fetchall()
        payload = "\n".join(
            f"{row['source_relative_path']}\0{row['source_revision']}\0"
            f"{row['ordinal']}\0{row['content_hash']}"
            for row in rows
        )
        return "kcorpus_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def resolve_citations(
        self,
        connection: sqlite3.Connection,
        citation_ids: tuple[str, ...],
        *,
        visibility: str = "private",
    ) -> tuple[tuple[str, KnowledgeChunk], ...]:
        """Resolve only current citations so stale evidence cannot be redeposited."""

        if not citation_ids or len(citation_ids) > 8:
            raise ValueError("knowledge learning requires between 1 and 8 citations")
        if len(set(citation_ids)) != len(citation_ids):
            raise ValueError("knowledge learning citations must be unique")
        if visibility not in {"private", "public"}:
            raise ValueError("invalid knowledge visibility")
        rows = connection.execute(
            """
            SELECT * FROM knowledge_chunks
            WHERE workspace_id=? AND visibility=? AND active=1
            """,
            (self.workspace_id, visibility),
        ).fetchall()
        resolved: dict[str, KnowledgeChunk] = {}
        for row in rows:
            chunk = self._chunk(row)
            candidate = citation_id(chunk)
            if candidate in citation_ids:
                resolved[candidate] = chunk
        missing = [item for item in citation_ids if item not in resolved]
        if missing:
            raise KeyError("knowledge learning citation is stale or unknown")
        return tuple((item, resolved[item]) for item in citation_ids)

    def representative_chunks(
        self,
        connection: sqlite3.Connection,
        page_revisions: tuple[str, ...],
        *,
        visibility: str = "private",
        source_ids: tuple[str, ...] = (),
        per_page: int = 1,
    ) -> tuple[KnowledgeChunk, ...]:
        """Load bounded current chunks for graph-expanded page revisions."""

        if not page_revisions or len(page_revisions) > 50:
            raise ValueError("knowledge relation targets must contain 1 to 50 revisions")
        if per_page < 1 or per_page > 3:
            raise ValueError("knowledge relation chunks per page must be between 1 and 3")
        where, params = self._filters(
            visibility=visibility,
            source_ids=source_ids,
            page_revisions=page_revisions,
        )
        rows = connection.execute(
            f"SELECT chunk.* FROM knowledge_chunks AS chunk WHERE {where} "
            "ORDER BY chunk.page_revision, chunk.ordinal, chunk.chunk_id",
            params,
        ).fetchall()
        counts: dict[str, int] = {}
        selected: list[KnowledgeChunk] = []
        for row in rows:
            revision = str(row["page_revision"])
            count = counts.get(revision, 0)
            if count >= per_page:
                continue
            selected.append(self._chunk(row))
            counts[revision] = count + 1
        return tuple(selected)

    def _insert_chunk(
        self, connection: sqlite3.Connection, chunk: KnowledgeChunk, created_at: str
    ) -> None:
        connection.execute(
            """
            INSERT INTO knowledge_chunks (
                chunk_id, workspace_id, page_id, page_revision, page_path,
                source_id, source_revision, source_kind, source_relative_path,
                proposal_id, artifact_id, block_id, ordinal, title,
                heading_path_json, page_number, block_kind, bbox_json, media_ref,
                confidence, parser_id, parser_version, text, token_count, content_hash,
                visibility, language, active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk.chunk_id,
                chunk.workspace_id,
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
                json.dumps(chunk.heading_path, ensure_ascii=False, separators=(",", ":")),
                chunk.page_number,
                chunk.block_kind,
                (json.dumps(chunk.bbox, separators=(",", ":")) if chunk.bbox is not None else None),
                chunk.media_ref,
                chunk.confidence,
                chunk.parser_id,
                chunk.parser_version,
                chunk.text,
                chunk.token_count,
                chunk.content_hash,
                chunk.visibility,
                chunk.language,
                int(chunk.active),
                created_at,
            ),
        )
        connection.execute(
            "INSERT INTO knowledge_chunks_fts (chunk_id, terms) VALUES (?, ?)",
            (chunk.chunk_id, index_text(chunk, ablation_policy=self.ablation_policy)),
        )
        value = embedding_text(chunk, ablation_policy=self.ablation_policy)
        vector = self.embedding_provider.embed(value)
        connection.execute(
            """
            INSERT INTO knowledge_chunk_embeddings (
                chunk_id, model_id, model_revision, dimensions,
                input_hash, vector_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chunk.chunk_id,
                self.embedding_provider.model_id,
                self.embedding_provider.model_revision,
                self.embedding_provider.dimensions,
                "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest(),
                serialize_vector(vector),
                created_at,
            ),
        )

    def _filters(
        self,
        *,
        visibility: str,
        source_ids: tuple[str, ...],
        page_revisions: tuple[str, ...],
    ) -> tuple[str, tuple[object, ...]]:
        clauses = ["chunk.workspace_id=?", "chunk.visibility=?"]
        params: list[object] = [self.workspace_id, visibility]
        if source_ids:
            clauses.append("chunk.source_id IN (" + ",".join("?" for _ in source_ids) + ")")
            params.extend(source_ids)
        if page_revisions:
            clauses.append("chunk.page_revision IN (" + ",".join("?" for _ in page_revisions) + ")")
            params.extend(page_revisions)
        else:
            clauses.append("chunk.active=1")
        return " AND ".join(clauses), tuple(params)

    @staticmethod
    def _chunk(row: sqlite3.Row) -> KnowledgeChunk:
        heading_path = json.loads(str(row["heading_path_json"]))
        if not isinstance(heading_path, list):
            raise ValueError("invalid knowledge chunk heading path")
        raw_bbox = json.loads(str(row["bbox_json"])) if row["bbox_json"] else None
        if raw_bbox is not None and (not isinstance(raw_bbox, list) or len(raw_bbox) != 4):
            raise ValueError("invalid knowledge chunk bbox")
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
            block_kind=str(row["block_kind"]),  # type: ignore[arg-type]
            bbox=(tuple(float(value) for value in raw_bbox) if raw_bbox is not None else None),  # type: ignore[arg-type]
            media_ref=str(row["media_ref"]) if row["media_ref"] else None,
            confidence=float(row["confidence"]),
            parser_id=str(row["parser_id"]),
            parser_version=str(row["parser_version"]),
        )
