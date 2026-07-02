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
    embedder_model = os.environ.get("MEM0_EMBEDDER_MODEL", "BAAI/bge-large-zh-v1.5")

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
                "base_url": deepseek_base_url,
                "model": deepseek_model,
            },
        },
        "embedder": {
            "provider": "huggingface",
            "config": {
                "model": embedder_model,
            },
        },
    }
