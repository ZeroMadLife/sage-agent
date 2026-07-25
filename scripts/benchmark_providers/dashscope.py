"""DashScope text embedding adapter for Benchmark v2.

The API key is read only from ``DASHSCOPE_API_KEY``. This module never reads
project files, Keychain entries, or persistent caches.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

_API_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
_MODEL = "text-embedding-v4"
_BATCH_SIZE = 10


class DashScopeEmbeddingProvider:
    model_id = "dashscope.text-embedding-v4"
    model_revision = "api-v1"
    dimensions = 1024
    supports_semantic_recall = True

    def __init__(self) -> None:
        self._api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if not self._api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is required")
        self._cache: dict[str, tuple[float, ...]] = {}

    def prepare(self, texts: tuple[str, ...]) -> None:
        pending = [text for text in texts if text not in self._cache]
        for offset in range(0, len(pending), _BATCH_SIZE):
            batch = pending[offset : offset + _BATCH_SIZE]
            vectors = self._request(batch)
            if len(vectors) != len(batch):
                raise RuntimeError("DashScope embedding response count mismatch")
            self._cache.update(zip(batch, vectors, strict=True))

    def embed(self, text: str) -> tuple[float, ...]:
        cached = self._cache.get(text)
        if cached is not None:
            return cached
        vector = self._request([text])[0]
        self._cache[text] = vector
        return vector

    def _request(self, texts: list[str]) -> list[tuple[float, ...]]:
        payload = json.dumps(
            {"model": _MODEL, "input": texts, "dimensions": self.dimensions}
        ).encode("utf-8")
        request = urllib.request.Request(
            _API_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = json.loads(response.read())
                ordered = sorted(raw["data"], key=lambda item: int(item["index"]))
                vectors = [tuple(float(value) for value in item["embedding"]) for item in ordered]
                if any(len(vector) != self.dimensions for vector in vectors):
                    raise RuntimeError("DashScope embedding dimensions changed")
                return vectors
            except (
                urllib.error.URLError,
                TimeoutError,
                OSError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
        raise RuntimeError("DashScope embedding request failed") from last_error


def create_provider() -> DashScopeEmbeddingProvider:
    return DashScopeEmbeddingProvider()
