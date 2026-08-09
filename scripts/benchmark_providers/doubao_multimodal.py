"""Doubao multimodal embedding adapter for text-only retrieval benchmarks.

The multimodal endpoint fuses one request into one vector. Long-book indexing
therefore sends exactly one text chunk per request and parallelizes only across
independent requests. The API key is read from ``DOUBAO_EMBEDDING_KEY``.
"""

from __future__ import annotations

import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal"
_MODEL = "doubao-embedding-vision-250615"
_DIMENSIONS = 2_048


class DoubaoMultimodalEmbeddingProvider:
    """Symmetric 2048-dimensional text vectors from Doubao's fusion endpoint."""

    model_id = f"doubao-multimodal.{_MODEL}"
    model_revision = f"{_MODEL}@2026-08-09+text-single-vector"
    dimensions = _DIMENSIONS
    supports_semantic_recall = True
    protocol_mode = "multimodal-text-single-vector"
    estimated_cost_usd = None

    def __init__(self) -> None:
        api_key = os.environ.get("DOUBAO_EMBEDDING_KEY", "").strip()
        if not api_key:
            raise RuntimeError("DOUBAO_EMBEDDING_KEY is required")
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
        )
        self._max_workers = _positive_int_env("SAGE_DOUBAO_EMBEDDING_MAX_WORKERS", default=8)
        self._cache: dict[str, tuple[float, ...]] = {}
        self._metrics_lock = threading.Lock()
        self.request_count = 0
        self.input_tokens = 0
        self.request_latencies_ms: list[float] = []

    def prepare(self, texts: tuple[str, ...]) -> None:
        pending = tuple(text for text in dict.fromkeys(texts) if text not in self._cache)
        if not pending:
            return
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            vectors = tuple(executor.map(self._request, pending))
        self._cache.update(zip(pending, vectors, strict=True))

    def prepare_documents(self, texts: tuple[str, ...]) -> None:
        self.prepare(texts)

    def prepare_queries(self, texts: tuple[str, ...]) -> None:
        self.prepare(texts)

    def embed(self, text: str) -> tuple[float, ...]:
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        vector = self._request(text)
        self._cache[text] = vector
        return vector

    def embed_document(self, text: str) -> tuple[float, ...]:
        return self.embed(text)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self.embed(text)

    def _request(self, text: str) -> tuple[float, ...]:
        started_at = time.perf_counter()
        payload = _post_with_retry(
            self._client,
            request_json={
                "model": _MODEL,
                "input": [{"type": "text", "text": text}],
            },
        )
        try:
            data = payload["data"]
            if not isinstance(data, dict):
                raise TypeError("Doubao embedding data must be an object")
            vector = _normalized_vector(data["embedding"], self.dimensions)
            usage = payload.get("usage", {})
            raw_tokens = usage.get("prompt_tokens", 0) if isinstance(usage, dict) else 0
            input_tokens = raw_tokens if isinstance(raw_tokens, int) and raw_tokens >= 0 else 0
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Doubao multimodal embedding response is invalid") from exc
        latency_ms = (time.perf_counter() - started_at) * 1_000
        with self._metrics_lock:
            self.request_count += 1
            self.input_tokens += input_tokens
            self.request_latencies_ms.append(latency_ms)
        return vector


def _post_with_retry(
    client: httpx.Client,
    *,
    request_json: dict[str, Any],
    max_attempts: int = 3,
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
                raise RuntimeError("Doubao multimodal embedding request failed") from exc
            failure = exc
        except httpx.TransportError as exc:
            failure = exc
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Doubao multimodal embedding request failed") from exc
        if attempt + 1 < max_attempts:
            time.sleep(0.25 * (2**attempt))
    raise RuntimeError("Doubao multimodal embedding request failed") from failure


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


def _positive_int_env(name: str, *, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = int(raw)
    if not 1 <= value <= 32:
        raise ValueError(f"{name} must be between 1 and 32")
    return value


def create_provider() -> DoubaoMultimodalEmbeddingProvider:
    return DoubaoMultimodalEmbeddingProvider()
