"""Mem0 client factory with Qdrant-backed graceful degradation."""

import logging
import os
from typing import Any

from qdrant_client import QdrantClient

logger = logging.getLogger(__name__)

try:
    from mem0 import Memory as Mem0Client
except Exception:  # pragma: no cover - exercised through create_mem0_client fallback.
    Mem0Client = None


def create_mem0_client() -> Any | None:
    """Create a Mem0 client or return None when the memory stack is unavailable."""
    try:
        if Mem0Client is None:
            raise RuntimeError("mem0 SDK is unavailable")

        config = _build_mem0_config()
        client = Mem0Client.from_config(config)
        logger.info("Mem0 client initialized")
        return client
    except Exception as exc:
        logger.warning("Mem0 initialization failed; long-term memory disabled: %s", exc)
        return None


def _build_mem0_config() -> dict[str, Any]:
    """Build Mem0 config from environment variables."""
    qdrant_host = os.environ.get("QDRANT_HOST", "localhost")
    qdrant_port = int(os.environ.get("QDRANT_PORT", "6333"))
    deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    deepseek_base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    deepseek_model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    embedder_provider = os.environ.get("MEM0_EMBEDDER_PROVIDER", "huggingface")
    embedder_model = _resolve_embedder_model(embedder_provider)
    embedder_dims = _resolve_embedder_dims(embedder_provider, embedder_model)

    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "client": QdrantClient(host=qdrant_host, port=qdrant_port, trust_env=False),
                "embedding_model_dims": embedder_dims,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "api_key": deepseek_api_key,
                "openai_base_url": deepseek_base_url,
                "model": deepseek_model,
                "top_p": 1.0,
            },
        },
        "embedder": _build_embedder_config(embedder_provider, embedder_model, embedder_dims),
    }


def _resolve_embedder_model(provider: str) -> str:
    """Resolve the embedder model with provider-specific defaults."""
    default_model = "text-embedding-3-small" if provider == "openai" else "BAAI/bge-large-zh-v1.5"
    return os.environ.get("MEM0_EMBEDDER_MODEL", default_model)


def _resolve_embedder_dims(provider: str, model: str) -> int:
    """Resolve vector dimensions for Qdrant and Mem0 embedder configuration."""
    if dims := os.environ.get("MEM0_EMBEDDER_DIMS"):
        return int(dims)

    known_dims = {
        "BAAI/bge-large-zh-v1.5": 1024,
        "BAAI/bge-small-zh-v1.5": 512,
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
    }
    if model in known_dims:
        return known_dims[model]

    return 1536 if provider == "openai" else 1024


def _build_embedder_config(provider: str, model: str, dims: int) -> dict[str, Any]:
    """Build Mem0 embedder configuration.

    HuggingFace remains the default for local/private embeddings. OpenAI-compatible
    embeddings can be enabled to avoid first-run HuggingFace model downloads.
    """
    if provider != "openai":
        return {
            "provider": provider,
            "config": {
                "model": model,
                "embedding_dims": dims,
            },
        }

    config: dict[str, Any] = {
        "api_key": os.environ.get("MEM0_EMBEDDER_API_KEY")
        or os.environ.get("OPENAI_PROXY_API_KEY")
        or os.environ.get("OPENAI_API_KEY", ""),
        "model": model,
        "openai_base_url": os.environ.get("MEM0_EMBEDDER_BASE_URL")
        or os.environ.get("OPENAI_PROXY_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "embedding_dims": dims,
    }

    return {
        "provider": "openai",
        "config": config,
    }
