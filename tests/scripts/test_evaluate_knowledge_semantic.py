from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.knowledge.embeddings import (
    DashScopeEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
)
from scripts.evaluate_knowledge_semantic import _build_provider


def test_build_provider_selects_native_dashscope_role_contract() -> None:
    provider = _build_provider(
        SimpleNamespace(
            provider="dashscope",
            embedding_api_key="test-only",
            embedding_base_url="https://workspace.example/api/v1",
            embedding_model="text-embedding-v4",
            embedding_model_revision="text-embedding-v4@2026-07-28",
            embedding_dimensions=1024,
            embedding_batch_size=10,
            embedding_timeout_seconds=30.0,
            query_instruct=(
                "Given a technical documentation query, retrieve relevant official documentation"
            ),
            cost_per_1k_tokens_usd=0.0001,
        )
    )

    assert isinstance(provider, DashScopeEmbeddingProvider)
    assert provider.dimensions == 1024
    assert "asymmetric-query-document" in provider.model_revision


def test_build_provider_selects_openai_compatible_endpoint() -> None:
    provider = _build_provider(
        SimpleNamespace(
            provider="openai_compatible",
            embedding_api_key="test-only",
            embedding_base_url="https://ark.example/api/coding/v3",
            embedding_model="doubao-embedding-vision-250615",
            embedding_model_revision="doubao-embedding-vision-250615@coding-v3",
            embedding_dimensions=2048,
            embedding_batch_size=10,
            embedding_timeout_seconds=30.0,
            query_instruct="",
            cost_per_1k_tokens_usd=None,
        )
    )

    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)
    assert provider.dimensions == 2048
    assert provider.estimated_cost_usd is None


def test_build_provider_rejects_missing_cloud_configuration() -> None:
    with pytest.raises(ValueError, match="requires API key, base URL, model, and revision"):
        _build_provider(
            SimpleNamespace(
                provider="dashscope",
                embedding_api_key="",
                embedding_base_url="",
                embedding_model="",
                embedding_model_revision="",
                embedding_dimensions=1024,
                embedding_batch_size=10,
                embedding_timeout_seconds=30.0,
                query_instruct="",
                cost_per_1k_tokens_usd=None,
            )
        )
