"""Pinned offline FastEmbed provider for reproducible local benchmarks."""

from __future__ import annotations

from pathlib import Path

from core.knowledge.embeddings import (
    DEFAULT_FASTEMBED_DIMENSIONS,
    DEFAULT_FASTEMBED_MODEL,
    DEFAULT_FASTEMBED_MODEL_REVISION,
    DEFAULT_FASTEMBED_REPOSITORY,
    FastEmbedEmbeddingConfig,
    FastEmbedEmbeddingProvider,
)


def create_provider() -> FastEmbedEmbeddingProvider:
    return FastEmbedEmbeddingProvider(
        FastEmbedEmbeddingConfig(
            model=DEFAULT_FASTEMBED_MODEL,
            repository=DEFAULT_FASTEMBED_REPOSITORY,
            model_revision=DEFAULT_FASTEMBED_MODEL_REVISION,
            dimensions=DEFAULT_FASTEMBED_DIMENSIONS,
            cache_dir=Path("~/.cache/sage/fastembed"),
            batch_size=32,
            local_files_only=True,
        )
    )
