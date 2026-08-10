"""Shared runtime assembly for version-bound Knowledge embedding providers."""

from __future__ import annotations

from pathlib import Path

from core.config.settings import Settings
from core.knowledge.embeddings import (
    DashScopeEmbeddingConfig,
    DashScopeEmbeddingProvider,
    DoubaoMultimodalEmbeddingConfig,
    DoubaoMultimodalEmbeddingProvider,
    FastEmbedEmbeddingConfig,
    FastEmbedEmbeddingProvider,
    OpenAICompatibleEmbeddingConfig,
    OpenAICompatibleEmbeddingProvider,
)
from core.knowledge.retrieval import DenseEmbeddingProvider, HashingEmbeddingProvider


def build_knowledge_embedding_provider(
    settings: Settings,
    *,
    hashing_dimensions: int = 256,
) -> DenseEmbeddingProvider:
    provider_name = settings.knowledge_embedding_provider.strip().casefold()
    if provider_name == "doubao_multimodal":
        return DoubaoMultimodalEmbeddingProvider(
            DoubaoMultimodalEmbeddingConfig(
                api_key=settings.knowledge_embedding_api_key,
                base_url=settings.knowledge_embedding_base_url,
                model=settings.knowledge_embedding_model,
                model_revision=settings.knowledge_embedding_model_revision,
                dimensions=settings.knowledge_embedding_dimensions,
                timeout_seconds=settings.knowledge_embedding_timeout_seconds,
                max_workers=settings.knowledge_doubao_max_workers,
                cost_per_1k_tokens_usd=settings.knowledge_embedding_cost_per_1k_tokens_usd,
            )
        )
    if provider_name == "dashscope":
        return DashScopeEmbeddingProvider(
            DashScopeEmbeddingConfig(
                api_key=settings.knowledge_embedding_api_key,
                base_url=settings.knowledge_embedding_base_url,
                model=settings.knowledge_embedding_model,
                model_revision=settings.knowledge_embedding_model_revision,
                dimensions=settings.knowledge_embedding_dimensions,
                query_instruct=settings.knowledge_embedding_query_instruct,
                batch_size=settings.knowledge_dashscope_batch_size,
                timeout_seconds=settings.knowledge_embedding_timeout_seconds,
                cost_per_1k_tokens_usd=settings.knowledge_embedding_cost_per_1k_tokens_usd,
            )
        )
    if provider_name == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider(
            OpenAICompatibleEmbeddingConfig(
                api_key=settings.knowledge_embedding_api_key,
                base_url=settings.knowledge_embedding_base_url,
                model=settings.knowledge_embedding_model,
                model_revision=settings.knowledge_embedding_model_revision,
                dimensions=settings.knowledge_embedding_dimensions,
                batch_size=settings.knowledge_embedding_batch_size,
                timeout_seconds=settings.knowledge_embedding_timeout_seconds,
                cost_per_1k_tokens_usd=settings.knowledge_embedding_cost_per_1k_tokens_usd,
            )
        )
    if provider_name == "fastembed":
        return FastEmbedEmbeddingProvider(
            FastEmbedEmbeddingConfig(
                model=settings.knowledge_fastembed_model,
                repository=settings.knowledge_fastembed_repository,
                model_revision=settings.knowledge_fastembed_model_revision,
                dimensions=settings.knowledge_fastembed_dimensions,
                cache_dir=Path(settings.knowledge_fastembed_cache_dir),
                batch_size=settings.knowledge_fastembed_batch_size,
                local_files_only=settings.knowledge_fastembed_local_files_only,
            )
        )
    if provider_name == "hashing":
        return HashingEmbeddingProvider(dimensions=hashing_dimensions)
    raise ValueError("unknown Knowledge embedding provider")


__all__ = ["build_knowledge_embedding_provider"]
