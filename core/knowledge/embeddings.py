"""Production embedding adapters for the rebuildable Knowledge index."""

from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
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

    def prepare(self, texts: tuple[str, ...]) -> None:
        pending = tuple(text for text in dict.fromkeys(texts) if text not in self._cache)
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

    def _load_model(self) -> Any:
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
class OpenAICompatibleEmbeddingConfig:
    api_key: str
    base_url: str
    model: str
    dimensions: int
    model_revision: str = "api-v1"
    batch_size: int = 32
    timeout_seconds: float = 30.0

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
    "FastEmbedEmbeddingConfig",
    "FastEmbedEmbeddingProvider",
    "OpenAICompatibleEmbeddingConfig",
    "OpenAICompatibleEmbeddingProvider",
]
