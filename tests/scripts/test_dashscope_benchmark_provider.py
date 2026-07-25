from __future__ import annotations

import http.client
import json
from io import BytesIO
from typing import Any

import pytest

from scripts.benchmark_providers import dashscope


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._stream = BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._stream.read()


def test_provider_batches_caches_and_restores_response_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only")
    calls: list[list[str]] = []

    def fake_urlopen(request: Any, *, timeout: int) -> _Response:
        assert timeout == 30
        payload = json.loads(request.data)
        assert payload["model"] == "text-embedding-v4"
        assert payload["dimensions"] == 1024
        texts = list(payload["input"])
        calls.append(texts)
        data = [
            {"index": index, "embedding": [float(index)] * 1024}
            for index in reversed(range(len(texts)))
        ]
        return _Response({"data": data})

    monkeypatch.setattr(dashscope.urllib.request, "urlopen", fake_urlopen)
    provider = dashscope.DashScopeEmbeddingProvider()

    assert provider.model_id == "dashscope.text-embedding-v4"

    provider.prepare(tuple(f"text-{index}" for index in range(12)))

    assert [len(batch) for batch in calls] == [10, 2]
    assert provider.embed("text-0") == (0.0,) * 1024
    assert provider.embed("text-11") == (1.0,) * 1024
    assert len(calls) == 2


def test_provider_retries_truncated_responses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only")
    attempts = 0
    sleeps: list[int] = []

    def fake_urlopen(request: Any, *, timeout: int) -> _Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise http.client.IncompleteRead(b"partial", 4)
        return _Response({"data": [{"index": 0, "embedding": [0.5] * 1024}]})

    monkeypatch.setattr(dashscope.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(dashscope.time, "sleep", sleeps.append)

    vector = dashscope.DashScopeEmbeddingProvider().embed("retry-me")

    assert vector == (0.5,) * 1024
    assert attempts == 2
    assert sleeps == [1]
