from __future__ import annotations

from typing import Any

import pytest

from core.knowledge.embeddings import (
    OpenAICompatibleEmbeddingConfig,
    OpenAICompatibleEmbeddingProvider,
)


class _Response:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("request failed")

    def json(self) -> dict[str, Any]:
        return self._payload


def test_openai_compatible_provider_batches_normalizes_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(*_args: Any, **kwargs: Any) -> _Response:
        calls.append(kwargs)
        values = kwargs["json"]["input"]
        return _Response(
            {
                "data": [
                    {"index": index, "embedding": [3.0, 4.0]} for index, _value in enumerate(values)
                ]
            }
        )

    monkeypatch.setattr("core.knowledge.embeddings.httpx.post", fake_post)
    provider = OpenAICompatibleEmbeddingProvider(
        OpenAICompatibleEmbeddingConfig(
            api_key="secret",
            base_url="https://embedding.example/v1",
            model="embedding-model",
            dimensions=2,
            batch_size=2,
        )
    )

    provider.prepare(("first", "second", "third"))

    assert provider.embed("first") == pytest.approx((0.6, 0.8))
    assert provider.embed("third") == pytest.approx((0.6, 0.8))
    assert len(calls) == 2
    assert [call["json"]["input"] for call in calls] == [
        ["first", "second"],
        ["third"],
    ]
    assert all(call["headers"]["Authorization"] == "Bearer secret" for call in calls)


@pytest.mark.parametrize("indexes", ([0, 0], [0, 2]))
def test_openai_compatible_provider_rejects_invalid_response_indexes(
    monkeypatch: pytest.MonkeyPatch,
    indexes: list[int],
) -> None:
    def fake_post(*_args: Any, **_kwargs: Any) -> _Response:
        return _Response({"data": [{"index": index, "embedding": [3.0, 4.0]} for index in indexes]})

    monkeypatch.setattr("core.knowledge.embeddings.httpx.post", fake_post)
    provider = OpenAICompatibleEmbeddingProvider(
        OpenAICompatibleEmbeddingConfig(
            api_key="secret",
            base_url="https://embedding.example/v1",
            model="embedding-model",
            dimensions=2,
        )
    )

    with pytest.raises(RuntimeError, match="embedding request failed"):
        provider.prepare(("first", "second"))


@pytest.mark.parametrize(
    ("base_url", "message"),
    [
        ("http://embedding.example/v1", "HTTPS"),
        ("file:///tmp/embeddings", "HTTP"),
    ],
)
def test_openai_compatible_provider_rejects_unsafe_remote_base_urls(
    base_url: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        OpenAICompatibleEmbeddingConfig(
            api_key="secret",
            base_url=base_url,
            model="embedding-model",
            dimensions=2,
        )


def test_openai_compatible_provider_allows_local_http() -> None:
    config = OpenAICompatibleEmbeddingConfig(
        api_key="local-secret",
        base_url="http://127.0.0.1:11434/v1",
        model="local-embedding",
        dimensions=384,
    )

    assert config.base_url == "http://127.0.0.1:11434/v1"
