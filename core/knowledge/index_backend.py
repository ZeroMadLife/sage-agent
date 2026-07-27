"""Shared contract for rebuildable Knowledge retrieval projections."""

from __future__ import annotations

import sqlite3
from typing import Protocol

from core.knowledge.relevance import KnowledgeRelevancePolicy
from core.knowledge.retrieval import (
    DenseEmbeddingProvider,
    KnowledgeChunk,
    KnowledgeIndexSummary,
    KnowledgeRetrievalMode,
    KnowledgeSearchHit,
)


class KnowledgeIndexBackend(Protocol):
    """Project canonical SQLite revisions into one replaceable search backend."""

    workspace_id: str
    embedding_provider: DenseEmbeddingProvider
    relevance_policy: KnowledgeRelevancePolicy | None

    @property
    def backend_id(self) -> str: ...

    def ensure_schema(self, connection: sqlite3.Connection) -> None: ...

    def backfill(self, connection: sqlite3.Connection, *, force: bool = False) -> None: ...

    def sync_revision_safely(self, connection: sqlite3.Connection, revision_id: str) -> bool: ...

    def summary(self, connection: sqlite3.Connection) -> KnowledgeIndexSummary: ...

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
    ) -> tuple[KnowledgeSearchHit, ...]: ...

    def corpus_revision(self, connection: sqlite3.Connection) -> str: ...

    def resolve_citations(
        self,
        connection: sqlite3.Connection,
        citation_ids: tuple[str, ...],
        *,
        visibility: str = "private",
    ) -> tuple[tuple[str, KnowledgeChunk], ...]: ...

    def representative_chunks(
        self,
        connection: sqlite3.Connection,
        page_revisions: tuple[str, ...],
        *,
        visibility: str = "private",
        source_ids: tuple[str, ...] = (),
        per_page: int = 1,
    ) -> tuple[KnowledgeChunk, ...]: ...
