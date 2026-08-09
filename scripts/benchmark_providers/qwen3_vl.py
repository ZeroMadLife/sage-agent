"""Qwen3-VL embedding adapter for text-only retrieval benchmarks.

``enable_fusion`` is deliberately disabled: each chunk must keep its own vector
and citation identity. The API key is read from ``BAILIAN_EMBEDDING_KEY``.
"""

from __future__ import annotations

import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

import httpx

from scripts.benchmark_providers._disk_cache import EmbeddingDiskCache

_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/embeddings/"
    "multimodal-embedding/multimodal-embedding"
)
_MODEL = "qwen3-vl-embedding"
_DIMENSIONS = 1_024
_QUERY_INSTRUCT = (
    "Given a learning question, retrieve passages from long books that provide "
    "the necessary evidence. 根据学习问题检索长书中可支持回答的原文证据。"
)


class Qwen3VLEmbeddingProvider:
    """Role-aware Qwen3-VL vectors using non-fused text batches."""

    model_id = f"dashscope-multimodal.{_MODEL}"
    model_revision = f"{_MODEL}@2026-08-09+non-fusion+query-instruct-v1"
    dimensions = _DIMENSIONS
    supports_semantic_recall = True
    protocol_mode = "multimodal-text-batch-non-fusion"
    estimated_cost_usd = None

    def __init__(self) -> None:
        api_key = os.environ.get("BAILIAN_EMBEDDING_KEY", "").strip()
        if not api_key:
            raise RuntimeError("BAILIAN_EMBEDDING_KEY is required")
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=90.0,
        )
        self._batch_size = _positive_int_env("SAGE_QWEN3_VL_BATCH_SIZE", default=10, maximum=10)
        self._max_workers = _positive_int_env("SAGE_QWEN3_VL_MAX_WORKERS", default=2, maximum=16)
        self._document_cache: dict[str, tuple[float, ...]] = {}
        self._query_cache: dict[str, tuple[float, ...]] = {}
        self._disk_cache = EmbeddingDiskCache(
            namespace=self.model_revision, dimensions=self.dimensions
        )
        self._metrics_lock = threading.Lock()
        self.cache_hit_count = 0
        self.request_count = 0
        self.input_tokens = 0
        self.request_latencies_ms: list[float] = []

    def prepare(self, texts: tuple[str, ...]) -> None:
        self.prepare_documents(texts)

    def prepare_documents(self, texts: tuple[str, ...]) -> None:
        self._prepare(texts, role="document")

    def prepare_queries(self, texts: tuple[str, ...]) -> None:
        self._prepare(texts, role="query")

    def embed(self, text: str) -> tuple[float, ...]:
        return self.embed_document(text)

    def embed_document(self, text: str) -> tuple[float, ...]:
        return self._embed(text, role="document")

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._embed(text, role="query")

    def _prepare(self, texts: tuple[str, ...], *, role: Literal["document", "query"]) -> None:
        cache = self._cache(role)
        pending: list[str] = []
        for text in dict.fromkeys(texts):
            if text in cache:
                continue
            cached = self._disk_cache.get(text, role=role)
            if cached is None:
                pending.append(text)
            else:
                cache[text] = cached
                self.cache_hit_count += 1
        batches = tuple(
            pending[offset : offset + self._batch_size]
            for offset in range(0, len(pending), self._batch_size)
        )
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            vectors_by_batch = tuple(
                executor.map(lambda batch: self._request_and_cache(batch, role=role), batches)
            )
        for batch, vectors in zip(batches, vectors_by_batch, strict=True):
            cache.update(zip(batch, vectors, strict=True))

    def _embed(self, text: str, *, role: Literal["document", "query"]) -> tuple[float, ...]:
        cache = self._cache(role)
        cached = cache.get(text)
        if cached is not None:
            return cached
        cached = self._disk_cache.get(text, role=role)
        if cached is not None:
            cache[text] = cached
            self.cache_hit_count += 1
            return cached
        vector = self._request_and_cache((text,), role=role)[0]
        cache[text] = vector
        return vector

    def _cache(self, role: Literal["document", "query"]) -> dict[str, tuple[float, ...]]:
        return self._document_cache if role == "document" else self._query_cache

    def _request(
        self,
        texts: tuple[str, ...],
        *,
        role: Literal["document", "query"],
    ) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        parameters: dict[str, object] = {
            "dimension": self.dimensions,
            "enable_fusion": False,
        }
        if role == "query":
            parameters["instruct"] = _QUERY_INSTRUCT
        started_at = time.perf_counter()
        payload = _post_with_retry(
            self._client,
            request_json={
                "model": _MODEL,
                "input": {"contents": [{"text": text} for text in texts]},
                "parameters": parameters,
            },
        )
        try:
            rows = payload["output"]["embeddings"]
            if not isinstance(rows, list):
                raise TypeError("Qwen3-VL embedding data must be a list")
            ordered = sorted(rows, key=lambda item: int(item["index"]))
            if len(ordered) != len(texts):
                raise ValueError("Qwen3-VL embedding response count mismatch")
            if [int(item["index"]) for item in ordered] != list(range(len(texts))):
                raise ValueError("Qwen3-VL embedding response indexes are invalid")
            vectors = tuple(
                _normalized_vector(item["embedding"], self.dimensions) for item in ordered
            )
            usage = payload.get("usage", {})
            raw_tokens = usage.get("input_tokens", 0) if isinstance(usage, dict) else 0
            input_tokens = raw_tokens if isinstance(raw_tokens, int) and raw_tokens >= 0 else 0
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Qwen3-VL embedding response is invalid") from exc
        with self._metrics_lock:
            self.request_count += 1
            self.input_tokens += input_tokens
            self.request_latencies_ms.append((time.perf_counter() - started_at) * 1_000)
        return vectors

    def _request_and_cache(
        self,
        texts: tuple[str, ...],
        *,
        role: Literal["document", "query"],
    ) -> tuple[tuple[float, ...], ...]:
        vectors = self._request(texts, role=role)
        for text, vector in zip(texts, vectors, strict=True):
            self._disk_cache.put(text, vector, role=role)
        return vectors


def _post_with_retry(
    client: httpx.Client,
    *,
    request_json: dict[str, Any],
    max_attempts: int = 5,
) -> dict[str, Any]:
    failure: Exception | None = None
    for attempt in range(max_attempts):
        try:
            response = client.post(_ENDPOINT, json=request_json)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("embedding response must be an object")
            return payload
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 429 and exc.response.status_code < 500:
                raise RuntimeError("Qwen3-VL embedding request failed") from exc
            failure = exc
        except httpx.TransportError as exc:
            failure = exc
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Qwen3-VL embedding request failed") from exc
        if attempt + 1 < max_attempts:
            time.sleep(0.25 * (2**attempt))
    raise RuntimeError("Qwen3-VL embedding request failed") from failure


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


def _positive_int_env(name: str, *, default: int, maximum: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def create_provider() -> Qwen3VLEmbeddingProvider:
    return Qwen3VLEmbeddingProvider()
