"""Build the configured rebuildable Knowledge retrieval backend."""

from __future__ import annotations

from core.knowledge.index import LocalKnowledgeIndex
from core.knowledge.index_backend import KnowledgeIndexBackend
from core.knowledge.observability import KnowledgeRetrievalObservabilityConfig
from core.knowledge.postgres_index import (
    PostgresKnowledgeIndex,
    PostgresKnowledgeIndexConfig,
)
from core.knowledge.relevance import KnowledgeRelevancePolicy
from core.knowledge.retrieval import DenseEmbeddingProvider


def build_knowledge_index(
    *,
    backend: str,
    workspace_id: str,
    postgres_dsn: str,
    postgres_connect_timeout_seconds: int,
    postgres_pool_max_connections: int,
    embedding_provider: DenseEmbeddingProvider | None = None,
    relevance_policy: KnowledgeRelevancePolicy | None = None,
    observability: KnowledgeRetrievalObservabilityConfig | None = None,
) -> KnowledgeIndexBackend:
    normalized = backend.strip().casefold()
    if normalized == "sqlite":
        return LocalKnowledgeIndex(
            workspace_id=workspace_id,
            embedding_provider=embedding_provider,
            relevance_policy=relevance_policy,
            observability=observability,
        )
    if normalized == "postgres":
        return PostgresKnowledgeIndex(
            PostgresKnowledgeIndexConfig(
                dsn=postgres_dsn,
                connect_timeout_seconds=postgres_connect_timeout_seconds,
                pool_max_connections=postgres_pool_max_connections,
            ),
            workspace_id=workspace_id,
            embedding_provider=embedding_provider,
            relevance_policy=relevance_policy,
            observability=observability,
        )
    raise ValueError("unknown Knowledge index backend")
