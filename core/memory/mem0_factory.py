"""Mem0 client factory with Qdrant-backed graceful degradation."""

import logging
import os
from typing import Any

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

    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "host": qdrant_host,
                "port": qdrant_port,
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
        "embedder": _build_embedder_config(embedder_provider),
    }


def _build_embedder_config(provider: str) -> dict[str, Any]:
    """Build Mem0 embedder configuration.

    HuggingFace remains the default for local/private embeddings. OpenAI-compatible
    embeddings can be enabled to avoid first-run HuggingFace model downloads.
    """
    if provider != "openai":
        model = os.environ.get("MEM0_EMBEDDER_MODEL", "BAAI/bge-large-zh-v1.5")
        return {
            "provider": provider,
            "config": {
                "model": model,
            },
        }

    model = os.environ.get("MEM0_EMBEDDER_MODEL", "text-embedding-3-small")
    config: dict[str, Any] = {
        "api_key": os.environ.get("MEM0_EMBEDDER_API_KEY")
        or os.environ.get("OPENAI_PROXY_API_KEY")
        or os.environ.get("OPENAI_API_KEY", ""),
        "model": model,
        "openai_base_url": os.environ.get("MEM0_EMBEDDER_BASE_URL")
        or os.environ.get("OPENAI_PROXY_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    }
    if dims := os.environ.get("MEM0_EMBEDDER_DIMS"):
        config["embedding_dims"] = int(dims)

    return {
        "provider": "openai",
        "config": config,
    }
