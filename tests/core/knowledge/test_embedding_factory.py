from __future__ import annotations

import pytest

from core.config.settings import Settings
from core.knowledge.embedding_factory import build_knowledge_embedding_provider
from core.knowledge.embeddings import DashScopeEmbeddingProvider
from core.knowledge.retrieval import HashingEmbeddingProvider


def test_embedding_factory_builds_configured_dashscope_provider() -> None:
    settings = Settings(
        _env_file=None,
        knowledge_embedding_provider="dashscope",
        knowledge_embedding_api_key="test-only",
        knowledge_embedding_base_url="https://workspace.example/api/v1",
        knowledge_embedding_model="text-embedding-v4",
        knowledge_embedding_model_revision="text-embedding-v4@2026-07-28",
        knowledge_embedding_dimensions=1024,
        knowledge_embedding_query_instruct=(
            "Given a technical documentation query, retrieve relevant official documentation"
        ),
        knowledge_embedding_cost_per_1k_tokens_usd=0.0001,
    )

    provider = build_knowledge_embedding_provider(settings)

    assert isinstance(provider, DashScopeEmbeddingProvider)
    assert provider.dimensions == 1024
    assert "asymmetric-query-document" in provider.model_revision


def test_embedding_factory_preserves_hashing_dimension_override() -> None:
    settings = Settings(_env_file=None, knowledge_embedding_provider="hashing")

    provider = build_knowledge_embedding_provider(settings, hashing_dimensions=64)

    assert isinstance(provider, HashingEmbeddingProvider)
    assert provider.dimensions == 64


def test_embedding_factory_rejects_unknown_provider() -> None:
    settings = Settings(_env_file=None, knowledge_embedding_provider="unknown")

    with pytest.raises(ValueError, match="unknown Knowledge embedding provider"):
        build_knowledge_embedding_provider(settings)
