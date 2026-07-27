from __future__ import annotations

import pytest

from core.config.settings import Settings
from core.knowledge.index import LocalKnowledgeIndex
from core.knowledge.index_factory import build_knowledge_index
from core.knowledge.observability import KnowledgeRetrievalObservabilityConfig
from core.knowledge.postgres_index import PostgresKnowledgeIndex
from core.knowledge.retrieval import HashingEmbeddingProvider


def test_knowledge_index_factory_keeps_sqlite_as_default() -> None:
    settings = Settings(_env_file=None)

    index = build_knowledge_index(
        backend=settings.knowledge_index_backend,
        workspace_id=settings.knowledge_workspace_id,
        postgres_dsn=settings.knowledge_postgres_dsn or settings.postgres_sync_dsn,
        postgres_connect_timeout_seconds=settings.knowledge_postgres_connect_timeout_seconds,
        postgres_pool_max_connections=settings.knowledge_postgres_pool_max_connections,
        embedding_provider=HashingEmbeddingProvider(dimensions=64),
    )

    assert isinstance(index, LocalKnowledgeIndex)
    assert index.workspace_id == "knowledge-local"
    assert index.observability.enabled is False


def test_knowledge_index_factory_binds_private_observability_config() -> None:
    config = KnowledgeRetrievalObservabilityConfig(
        enabled=True,
        hmac_key="factory-test-observability-key-value",
        max_candidates=12,
    )

    index = build_knowledge_index(
        backend="sqlite",
        workspace_id="knowledge-local",
        postgres_dsn="postgresql://unused:unused@localhost/unused",
        postgres_connect_timeout_seconds=5,
        postgres_pool_max_connections=4,
        embedding_provider=HashingEmbeddingProvider(dimensions=64),
        observability=config,
    )

    assert isinstance(index, LocalKnowledgeIndex)
    assert index.observability is config
    assert "factory-test-observability-key-value" not in repr(config)


def test_knowledge_index_factory_builds_lazy_postgres_backend_without_leaking_dsn() -> None:
    settings = Settings(
        _env_file=None,
        knowledge_index_backend="postgres",
        knowledge_workspace_id="knowledge-interview",
        knowledge_postgres_dsn="postgresql://user:secret@db.example/sage",
    )

    index = build_knowledge_index(
        backend=settings.knowledge_index_backend,
        workspace_id=settings.knowledge_workspace_id,
        postgres_dsn=settings.knowledge_postgres_dsn or settings.postgres_sync_dsn,
        postgres_connect_timeout_seconds=settings.knowledge_postgres_connect_timeout_seconds,
        postgres_pool_max_connections=settings.knowledge_postgres_pool_max_connections,
        embedding_provider=HashingEmbeddingProvider(dimensions=64),
    )

    assert isinstance(index, PostgresKnowledgeIndex)
    assert index.workspace_id == "knowledge-interview"
    assert "secret" not in repr(index.config)
    index.close()


def test_knowledge_index_factory_rejects_unknown_backend() -> None:
    with pytest.raises(ValueError, match="unknown Knowledge index backend"):
        build_knowledge_index(
            backend="qdrant",
            workspace_id="knowledge-local",
            postgres_dsn="postgresql://user:secret@db.example/sage",
            postgres_connect_timeout_seconds=5,
            postgres_pool_max_connections=4,
            embedding_provider=HashingEmbeddingProvider(dimensions=64),
        )
