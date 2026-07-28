"""Production embedding adapters for the rebuildable Knowledge index."""

from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from threading import RLock
from typing import Any
from urllib.parse import urlsplit

import httpx

FASTEMBED_RUNTIME_REVISION = "fastembed-0.8.0-mean-pooling"
DEFAULT_FASTEMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_FASTEMBED_REPOSITORY = "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
DEFAULT_FASTEMBED_MODEL_REVISION = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
DEFAULT_FASTEMBED_DIMENSIONS = 384
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class FastEmbedEmbeddingConfig:
    """Version-bound configuration for the local ONNX semantic provider."""

    model: str
    repository: str
    model_revision: str
    dimensions: int
    cache_dir: Path
    batch_size: int = 32
    local_files_only: bool = False

    def __post_init__(self) -> None:
        model = self.model.strip()
        repository = self.repository.strip()
        model_revision = self.model_revision.strip().casefold()
        if not model or len(model) > 200:
            raise ValueError("FastEmbed model is required")
        if (
            len(repository) > 200
            or repository.count("/") != 1
            or any(character.isspace() for character in repository)
        ):
            raise ValueError("FastEmbed repository must use owner/name format")
        if _COMMIT_PATTERN.fullmatch(model_revision) is None:
            raise ValueError("FastEmbed model revision must be a 40-character hexadecimal commit")
        if not 1 <= self.dimensions <= 8_192:
            raise ValueError("embedding dimensions must be between 1 and 8192")
        if not 1 <= self.batch_size <= 256:
            raise ValueError("embedding batch size must be between 1 and 256")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "repository", repository)
        object.__setattr__(self, "model_revision", model_revision)
        object.__setattr__(self, "cache_dir", self.cache_dir.expanduser().resolve())


class FastEmbedEmbeddingProvider:
    """Local semantic embeddings from an immutable Hugging Face ONNX snapshot."""

    supports_semantic_recall = True
    estimated_cost_usd = 0.0

    def __init__(self, config: FastEmbedEmbeddingConfig) -> None:
        self.config = config
        self.model_id = f"fastembed.{config.model}"
        self.model_revision = f"{config.model_revision}+{FASTEMBED_RUNTIME_REVISION}"
        self.dimensions = config.dimensions
        self._cache: dict[str, tuple[float, ...]] = {}
        self._model: Any | None = None
        self._model_lock = RLock()

    def prepare(self, texts: tuple[str, ...]) -> None:
        pending = tuple(text for text in dict.fromkeys(texts) if text not in self._cache)
        if not pending:
            return
        model = self._load_model()
        for offset in range(0, len(pending), self.config.batch_size):
            batch = pending[offset : offset + self.config.batch_size]
            try:
                raw_vectors = tuple(model.embed(batch, batch_size=self.config.batch_size))
                if len(raw_vectors) != len(batch):
                    raise ValueError("embedding response count mismatch")
                vectors = tuple(
                    _normalized_vector(list(raw), self.dimensions) for raw in raw_vectors
                )
            except (TypeError, ValueError, ArithmeticError) as exc:
                raise RuntimeError("FastEmbed embedding failed") from exc
            self._cache.update(zip(batch, vectors, strict=True))

    def embed(self, text: str) -> tuple[float, ...]:
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        self.prepare((text,))
        return self._cache[text]

    def prepare_documents(self, texts: tuple[str, ...]) -> None:
        self.prepare(texts)

    def prepare_queries(self, texts: tuple[str, ...]) -> None:
        self.prepare(texts)

    def embed_document(self, text: str) -> tuple[float, ...]:
        return self.embed(text)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self.embed(text)

    def _load_model(self) -> Any:
        with self._model_lock:
            if self._model is not None:
                return self._model
            try:
                fastembed = import_module("fastembed")
                if str(fastembed.__version__) != "0.8.0":
                    raise RuntimeError("unsupported FastEmbed runtime version")
                huggingface_hub = import_module("huggingface_hub")
                snapshot_path = huggingface_hub.snapshot_download(
                    repo_id=self.config.repository,
                    revision=self.config.model_revision,
                    cache_dir=str(self.config.cache_dir),
                    local_files_only=self.config.local_files_only,
                )
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message=r"The model .* now uses mean pooling instead of CLS embedding.*",
                        category=UserWarning,
                    )
                    self._model = fastembed.TextEmbedding(
                        model_name=self.config.model,
                        cache_dir=str(self.config.cache_dir),
                        specific_model_path=str(snapshot_path),
                    )
            except Exception as exc:
                raise RuntimeError("FastEmbed initialization failed") from exc
        return self._model


@dataclass(frozen=True, slots=True)
class DashScopeEmbeddingConfig:
    """Version-bound configuration for native DashScope retrieval embeddings."""

    api_key: str
    base_url: str
    model: str
    model_revision: str
    dimensions: int
    query_instruct: str = ""
    batch_size: int = 10
    timeout_seconds: float = 30.0
    cost_per_1k_tokens_usd: float | None = None

    def __post_init__(self) -> None:
        api_key = self.api_key.strip()
        base_url = self.base_url.strip().rstrip("/")
        model = self.model.strip()
        model_revision = self.model_revision.strip()
        query_instruct = self.query_instruct.strip()
        if not api_key or len(api_key) > 4_096:
            raise ValueError("DashScope API key is required")
        if not model or len(model) > 200:
            raise ValueError("DashScope embedding model is required")
        if not model_revision or len(model_revision) > 200:
            raise ValueError("DashScope embedding model revision is required")
        if not 1 <= self.dimensions <= 8_192:
            raise ValueError("embedding dimensions must be between 1 and 8192")
        if not 1 <= self.batch_size <= 10:
            raise ValueError("DashScope embedding batch size must be between 1 and 10")
        if not 1.0 <= self.timeout_seconds <= 120.0:
            raise ValueError("embedding timeout must be between 1 and 120 seconds")
        if len(query_instruct) > 1_000:
            raise ValueError("DashScope query instruct must not exceed 1000 characters")
        if self.cost_per_1k_tokens_usd is not None and not (
            0.0 <= self.cost_per_1k_tokens_usd <= 100.0
        ):
            raise ValueError("embedding cost per 1k tokens must be between 0 and 100 USD")
        parsed = urlsplit(base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("DashScope embedding base URL must use HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError(
                "DashScope embedding base URL must not contain credentials, query, or fragment"
            )
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "model_revision", model_revision)
        object.__setattr__(self, "query_instruct", query_instruct)


class DashScopeEmbeddingProvider:
    """Native DashScope adapter with asymmetric query and document embeddings."""

    supports_semantic_recall = True

    def __init__(self, config: DashScopeEmbeddingConfig) -> None:
        self.config = config
        self.model_id = f"dashscope.{config.model}"
        role_policy = "asymmetric-query-document"
        if config.query_instruct:
            role_policy += "+query-instruct-v1"
        self.model_revision = f"{config.model_revision}+{role_policy}"
        self.dimensions = config.dimensions
        self._document_cache: dict[str, tuple[float, ...]] = {}
        self._query_cache: dict[str, tuple[float, ...]] = {}
        self._total_tokens = 0

    @property
    def estimated_cost_usd(self) -> float | None:
        rate = self.config.cost_per_1k_tokens_usd
        if rate is None:
            return None
        return self._total_tokens / 1_000.0 * rate

    def prepare_documents(self, texts: tuple[str, ...]) -> None:
        self._prepare(texts, role="document")

    def prepare_queries(self, texts: tuple[str, ...]) -> None:
        self._prepare(texts, role="query")

    def embed_document(self, text: str) -> tuple[float, ...]:
        return self._embed(text, role="document")

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._embed(text, role="query")

    def prepare(self, texts: tuple[str, ...]) -> None:
        self.prepare_documents(texts)

    def embed(self, text: str) -> tuple[float, ...]:
        return self.embed_document(text)

    def _prepare(self, texts: tuple[str, ...], *, role: str) -> None:
        cache = self._cache(role)
        pending = tuple(text for text in dict.fromkeys(texts) if text not in cache)
        for offset in range(0, len(pending), self.config.batch_size):
            batch = pending[offset : offset + self.config.batch_size]
            vectors = self._request(batch, role=role)
            cache.update(zip(batch, vectors, strict=True))

    def _embed(self, text: str, *, role: str) -> tuple[float, ...]:
        cache = self._cache(role)
        cached = cache.get(text)
        if cached is not None:
            return cached
        vector = self._request((text,), role=role)[0]
        cache[text] = vector
        return vector

    def _cache(self, role: str) -> dict[str, tuple[float, ...]]:
        if role == "document":
            return self._document_cache
        if role == "query":
            return self._query_cache
        raise ValueError("unsupported embedding role")

    def _request(self, texts: tuple[str, ...], *, role: str) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        endpoint = (
            self.config.base_url
            if self.config.base_url.endswith("/services/embeddings/text-embedding/text-embedding")
            else (f"{self.config.base_url}/services/embeddings/" "text-embedding/text-embedding")
        )
        parameters: dict[str, object] = {
            "dimension": self.dimensions,
            "text_type": role,
            "output_type": "dense",
        }
        if role == "query" and self.config.query_instruct:
            parameters["instruct"] = self.config.query_instruct
        try:
            response = httpx.post(
                endpoint,
                json={
                    "model": self.config.model,
                    "input": {"texts": list(texts)},
                    "parameters": parameters,
                },
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload["output"]["embeddings"]
            if not isinstance(rows, list):
                raise TypeError("DashScope embedding data must be a list")
            ordered = sorted(rows, key=lambda item: int(item["text_index"]))
            if len(ordered) != len(texts):
                raise ValueError("DashScope embedding response count mismatch")
            if [int(item["text_index"]) for item in ordered] != list(range(len(texts))):
                raise ValueError("DashScope embedding response indexes are invalid")
            vectors = tuple(
                _normalized_vector(item["embedding"], self.dimensions) for item in ordered
            )
            usage = payload.get("usage", {})
            if isinstance(usage, dict):
                raw_tokens = usage.get("total_tokens", usage.get("input_tokens", 0))
                if isinstance(raw_tokens, int) and raw_tokens >= 0:
                    self._total_tokens += raw_tokens
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("DashScope embedding request failed") from exc
        return vectors


@dataclass(frozen=True, slots=True)
class OpenAICompatibleEmbeddingConfig:
    api_key: str
    base_url: str
    model: str
    dimensions: int
    model_revision: str = "api-v1"
    batch_size: int = 32
    timeout_seconds: float = 30.0
    cost_per_1k_tokens_usd: float | None = None

    def __post_init__(self) -> None:
        api_key = self.api_key.strip()
        base_url = self.base_url.strip().rstrip("/")
        model = self.model.strip()
        model_revision = self.model_revision.strip()
        if not api_key or len(api_key) > 4_096:
            raise ValueError("embedding API key is required")
        if not model or len(model) > 200:
            raise ValueError("embedding model is required")
        if not model_revision or len(model_revision) > 200:
            raise ValueError("embedding model revision is required")
        if not 1 <= self.dimensions <= 8_192:
            raise ValueError("embedding dimensions must be between 1 and 8192")
        if not 1 <= self.batch_size <= 256:
            raise ValueError("embedding batch size must be between 1 and 256")
        if not 1.0 <= self.timeout_seconds <= 120.0:
            raise ValueError("embedding timeout must be between 1 and 120 seconds")
        if self.cost_per_1k_tokens_usd is not None and not (
            0.0 <= self.cost_per_1k_tokens_usd <= 100.0
        ):
            raise ValueError("embedding cost per 1k tokens must be between 0 and 100 USD")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("embedding base URL must be HTTP or HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("embedding base URL must not contain credentials, query, or fragment")
        local_hosts = {"127.0.0.1", "::1", "localhost"}
        if parsed.scheme != "https" and parsed.hostname.casefold() not in local_hosts:
            raise ValueError("remote embedding base URL must use HTTPS")
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "base_url", base_url)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "model_revision", model_revision)


class OpenAICompatibleEmbeddingProvider:
    """Small cached adapter for OpenAI-compatible ``/embeddings`` endpoints."""

    supports_semantic_recall = True

    def __init__(self, config: OpenAICompatibleEmbeddingConfig) -> None:
        self.config = config
        self.model_id = f"openai-compatible.{config.model}"
        self.model_revision = config.model_revision
        self.dimensions = config.dimensions
        self._cache: dict[str, tuple[float, ...]] = {}
        self._total_tokens = 0

    @property
    def estimated_cost_usd(self) -> float | None:
        rate = self.config.cost_per_1k_tokens_usd
        if rate is None:
            return None
        return self._total_tokens / 1_000.0 * rate

    def prepare(self, texts: tuple[str, ...]) -> None:
        pending = tuple(text for text in dict.fromkeys(texts) if text not in self._cache)
        for offset in range(0, len(pending), self.config.batch_size):
            batch = pending[offset : offset + self.config.batch_size]
            vectors = self._request(batch)
            self._cache.update(zip(batch, vectors, strict=True))

    def embed(self, text: str) -> tuple[float, ...]:
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        vector = self._request((text,))[0]
        self._cache[text] = vector
        return vector

    def prepare_documents(self, texts: tuple[str, ...]) -> None:
        self.prepare(texts)

    def prepare_queries(self, texts: tuple[str, ...]) -> None:
        self.prepare(texts)

    def embed_document(self, text: str) -> tuple[float, ...]:
        return self.embed(text)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self.embed(text)

    def _request(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        endpoint = (
            self.config.base_url
            if self.config.base_url.endswith("/embeddings")
            else f"{self.config.base_url}/embeddings"
        )
        try:
            response = httpx.post(
                endpoint,
                json={
                    "model": self.config.model,
                    "input": list(texts),
                    "dimensions": self.dimensions,
                },
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload["data"]
            if not isinstance(rows, list):
                raise TypeError("embedding data must be a list")
            ordered = sorted(rows, key=lambda item: int(item["index"]))
            if len(ordered) != len(texts):
                raise ValueError("embedding response count mismatch")
            if [int(item["index"]) for item in ordered] != list(range(len(texts))):
                raise ValueError("embedding response indexes are invalid")
            vectors = tuple(
                _normalized_vector(item["embedding"], self.dimensions) for item in ordered
            )
            usage = payload.get("usage", {})
            if isinstance(usage, dict):
                raw_tokens = usage.get(
                    "prompt_tokens",
                    usage.get("input_tokens", usage.get("total_tokens", 0)),
                )
                if isinstance(raw_tokens, int) and raw_tokens >= 0:
                    self._total_tokens += raw_tokens
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("embedding request failed") from exc
        return vectors


def _normalized_vector(raw: object, dimensions: int) -> tuple[float, ...]:
    if not isinstance(raw, list) or len(raw) != dimensions:
        raise ValueError("embedding response dimensions changed")
    vector = tuple(float(value) for value in raw)
    if any(not math.isfinite(value) for value in vector):
        raise ValueError("embedding response contains non-finite values")
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0.0:
        raise ValueError("embedding response contains a zero vector")
    return tuple(value / norm for value in vector)


__all__ = [
    "DEFAULT_FASTEMBED_DIMENSIONS",
    "DEFAULT_FASTEMBED_MODEL",
    "DEFAULT_FASTEMBED_MODEL_REVISION",
    "DEFAULT_FASTEMBED_REPOSITORY",
    "FASTEMBED_RUNTIME_REVISION",
    "DashScopeEmbeddingConfig",
    "DashScopeEmbeddingProvider",
    "FastEmbedEmbeddingConfig",
    "FastEmbedEmbeddingProvider",
    "OpenAICompatibleEmbeddingConfig",
    "OpenAICompatibleEmbeddingProvider",
]
