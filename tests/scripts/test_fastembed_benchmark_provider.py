from __future__ import annotations

from core.knowledge.embeddings import (
    DEFAULT_FASTEMBED_DIMENSIONS,
    DEFAULT_FASTEMBED_MODEL,
    DEFAULT_FASTEMBED_MODEL_REVISION,
    DEFAULT_FASTEMBED_REPOSITORY,
    FastEmbedEmbeddingProvider,
)
from scripts.benchmark_providers.fastembed_local import create_provider


def test_local_fastembed_benchmark_provider_is_pinned_and_offline() -> None:
    provider = create_provider()

    assert isinstance(provider, FastEmbedEmbeddingProvider)
    assert provider.config.model == DEFAULT_FASTEMBED_MODEL
    assert provider.config.repository == DEFAULT_FASTEMBED_REPOSITORY
    assert provider.config.model_revision == DEFAULT_FASTEMBED_MODEL_REVISION
    assert provider.config.dimensions == DEFAULT_FASTEMBED_DIMENSIONS
    assert provider.config.cache_dir.name == "fastembed"
    assert provider.config.local_files_only is True
