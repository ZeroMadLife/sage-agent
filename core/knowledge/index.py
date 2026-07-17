"""Rebuildable SQLite FTS5 and dense index for the local knowledge workspace."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace

from core.knowledge.parsing import MarkdownParser, ParseRequest, deserialize_document
from core.knowledge.retrieval import (
    DenseEmbeddingProvider,
    HashingEmbeddingProvider,
    KnowledgeChunk,
    KnowledgeIndexSummary,
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
    error TEXT,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_index_revisions_status_idx
    ON knowledge_index_revisions(status, indexed_at);
CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
    chunk_id UNINDEXED,
    terms,
    tokenize='unicode61 remove_diacritics 2'
);
"""


class LocalKnowledgeIndex:
    """Local backend matching the future PostgreSQL FTS + pgvector contract."""

    backend_id = "sqlite-fts5+hashing"

    def __init__(
        self,
        *,
        workspace_id: str = "knowledge-local",
        embedding_provider: DenseEmbeddingProvider | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.embedding_provider = embedding_provider or HashingEmbeddingProvider()
        self._markdown_parser = MarkdownParser()

    def ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(_INDEX_SCHEMA)

    def backfill(self, connection: sqlite3.Connection, *, force: bool = False) -> None:
        if force:
            connection.execute("DELETE FROM knowledge_chunks_fts")
            connection.execute("DELETE FROM knowledge_chunk_embeddings")
            connection.execute("DELETE FROM knowledge_chunks")
            connection.execute("DELETE FROM knowledge_index_revisions")
        rows = connection.execute(
            """
            SELECT revision_id FROM knowledge_page_revisions AS revision
            WHERE ? OR revision_id NOT IN (
                SELECT page_revision FROM knowledge_index_revisions WHERE status='ready'
            )
            ORDER BY created_at, revision_id
            """,
            (int(force),),
        ).fetchall()
        for row in rows:
            self.sync_revision_safely(connection, str(row["revision_id"]))

    def sync_revision_safely(
        self, connection: sqlite3.Connection, revision_id: str
    ) -> bool:
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
                page_revision, status, chunk_count, error, indexed_at
            ) VALUES (?, 'ready', ?, NULL, ?)
            ON CONFLICT(page_revision) DO UPDATE SET
                status='ready', chunk_count=excluded.chunk_count,
                error=NULL, indexed_at=excluded.indexed_at
            """,
            (revision_id, len(chunks), str(row["created_at"])),
        )
        return len(chunks)

    def mark_error(
        self, connection: sqlite3.Connection, revision_id: str, message: str
    ) -> None:
        connection.execute(
            """
            INSERT INTO knowledge_index_revisions (
                page_revision, status, chunk_count, error, indexed_at
            ) VALUES (?, 'error', 0, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(page_revision) DO UPDATE SET
                status='error', chunk_count=0, error=excluded.error,
                indexed_at=excluded.indexed_at
            """,
            (revision_id, message[:500]),
        )

    def summary(self, connection: sqlite3.Connection) -> KnowledgeIndexSummary:
        revision_count = int(
            connection.execute("SELECT COUNT(*) FROM knowledge_page_revisions").fetchone()[0]
        )
        indexed_revision_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_index_revisions WHERE status='ready'"
            ).fetchone()[0]
        )
        active_chunk_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_chunks WHERE active=1"
            ).fetchone()[0]
        )
        total_chunk_count = int(
            connection.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
        )
        error_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_index_revisions WHERE status='error'"
            ).fetchone()[0]
        )
        return KnowledgeIndexSummary(
            backend=self.backend_id,
            embedding_model=self.embedding_provider.model_id,
            embedding_revision=self.embedding_provider.model_revision,
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
    ) -> tuple[KnowledgeSearchHit, ...]:
        if top_k < 1 or top_k > 50:
            raise ValueError("knowledge search top_k must be between 1 and 50")
        if len(source_ids) > 100 or len(page_revisions) > 100:
            raise ValueError("knowledge search filters are too large")
        if visibility not in {"private", "public"}:
            raise ValueError("invalid knowledge visibility")
        normalized = query.strip()
        if not normalized or len(normalized) > 2_000:
            raise ValueError("knowledge query must be between 1 and 2000 characters")
        candidate_limit = min(200, max(20, top_k * 5))
        where, filter_params = self._filters(
            visibility=visibility,
            source_ids=source_ids,
            page_revisions=page_revisions,
        )
        sparse_rows = connection.execute(
            f"""
            SELECT fts.chunk_id, bm25(knowledge_chunks_fts) AS score
            FROM knowledge_chunks_fts AS fts
            JOIN knowledge_chunks AS chunk ON chunk.chunk_id = fts.chunk_id
            WHERE knowledge_chunks_fts MATCH ? AND {where}
            ORDER BY score, fts.chunk_id
            LIMIT ?
            """,
            (fts_query(normalized), *filter_params, candidate_limit),
        ).fetchall()
        sparse = [
            (str(row["chunk_id"]), -float(row["score"])) for row in sparse_rows
        ]
        query_vector = self.embedding_provider.embed(normalized)
        dense_rows = connection.execute(
            f"""
            SELECT embedding.chunk_id, embedding.dimensions, embedding.vector_json
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
        dense = sorted(
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
                )
                for row in dense_rows
            ),
            key=lambda item: (-item[1], item[0]),
        )
        dense = [item for item in dense if item[1] > 0.0][:candidate_limit]
        if not self.embedding_provider.supports_semantic_recall:
            sparse_ids = {chunk_id for chunk_id, _score in sparse}
            dense = [item for item in dense if item[0] in sparse_ids]
        fused = reciprocal_rank_fusion(sparse, dense)[:top_k]
        chunk_ids = [item[0] for item in fused]
        if not chunk_ids:
            return ()
        placeholders = ",".join("?" for _ in chunk_ids)
        chunk_rows = connection.execute(
            f"SELECT * FROM knowledge_chunks WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        ).fetchall()
        chunks = {str(row["chunk_id"]): self._chunk(row) for row in chunk_rows}
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

    def _insert_chunk(
        self, connection: sqlite3.Connection, chunk: KnowledgeChunk, created_at: str
    ) -> None:
        connection.execute(
            """
            INSERT INTO knowledge_chunks (
                chunk_id, workspace_id, page_id, page_revision, page_path,
                source_id, source_revision, source_kind, source_relative_path,
                proposal_id, artifact_id, block_id, ordinal, title,
                heading_path_json, page_number, text, token_count, content_hash,
                visibility, language, active, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            (chunk.chunk_id, index_text(chunk)),
        )
        value = embedding_text(chunk)
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
            clauses.append(
                "chunk.page_revision IN (" + ",".join("?" for _ in page_revisions) + ")"
            )
            params.extend(page_revisions)
        else:
            clauses.append("chunk.active=1")
        return " AND ".join(clauses), tuple(params)

    @staticmethod
    def _chunk(row: sqlite3.Row) -> KnowledgeChunk:
        heading_path = json.loads(str(row["heading_path_json"]))
        if not isinstance(heading_path, list):
            raise ValueError("invalid knowledge chunk heading path")
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
