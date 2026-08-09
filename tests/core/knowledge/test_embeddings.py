from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from core.knowledge.embeddings import (
    FASTEMBED_RUNTIME_REVISION,
    DashScopeEmbeddingConfig,
    DashScopeEmbeddingProvider,
    DoubaoMultimodalEmbeddingConfig,
    DoubaoMultimodalEmbeddingProvider,
    FastEmbedEmbeddingConfig,
    FastEmbedEmbeddingProvider,
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
                ],
                "usage": {"prompt_tokens": len(values) * 5},
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
            cost_per_1k_tokens_usd=0.001,
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
    assert provider.estimated_cost_usd == pytest.approx(0.000015)


def test_doubao_multimodal_provider_keeps_one_text_per_vector_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(*_args: Any, **kwargs: Any) -> _Response:
        calls.append({"url": _args[0], **kwargs})
        return _Response(
            {
                "data": {"embedding": [3.0, 4.0]},
                "usage": {"prompt_tokens": 5},
            }
        )

    monkeypatch.setattr("core.knowledge.embeddings.httpx.post", fake_post)
    provider = DoubaoMultimodalEmbeddingProvider(
        DoubaoMultimodalEmbeddingConfig(
            api_key="secret",
            base_url="https://ark.example/api/v3",
            model="doubao-embedding-vision-250615",
            model_revision="doubao-embedding-vision-250615@2026-08-09",
            dimensions=2,
            max_workers=1,
            cost_per_1k_tokens_usd=0.001,
        )
    )

    provider.prepare_documents(("first", "second", "first"))

    assert provider.embed_query("first") == pytest.approx((0.6, 0.8))
    assert [call["json"]["input"] for call in calls] == [
        [{"type": "text", "text": "first"}],
        [{"type": "text", "text": "second"}],
    ]
    assert all(call["url"] == "https://ark.example/api/v3/embeddings/multimodal" for call in calls)
    assert provider.request_count == 2
    assert provider.input_tokens == 10
    assert provider.estimated_cost_usd == pytest.approx(0.00001)
    assert provider.protocol_mode == "multimodal-text-single-vector"


def test_doubao_multimodal_provider_rejects_unsafe_remote_base_url() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        DoubaoMultimodalEmbeddingConfig(
            api_key="secret",
            base_url="http://ark.example/api/v3",
            model="doubao-embedding-vision-250615",
            model_revision="doubao-embedding-vision-250615@2026-08-09",
            dimensions=2048,
        )


def test_openai_compatible_provider_retries_transient_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> _Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.RemoteProtocolError("transient disconnect")
        return _Response({"data": [{"index": 0, "embedding": [3.0, 4.0]}]})

    monkeypatch.setattr("core.knowledge.embeddings.httpx.post", fake_post)
    provider = OpenAICompatibleEmbeddingProvider(
        OpenAICompatibleEmbeddingConfig(
            api_key="secret",
            base_url="https://embedding.example/v1",
            model="embedding-model",
            dimensions=2,
            max_attempts=3,
            retry_backoff_seconds=0.0,
        )
    )

    assert provider.embed_query("retry") == pytest.approx((0.6, 0.8))
    assert calls == 2


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


def test_dashscope_provider_separates_document_and_query_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(*_args: Any, **kwargs: Any) -> _Response:
        calls.append(kwargs)
        texts = kwargs["json"]["input"]["texts"]
        role = kwargs["json"]["parameters"]["text_type"]
        base = 3.0 if role == "document" else 4.0
        return _Response(
            {
                "output": {
                    "embeddings": [
                        {
                            "text_index": index,
                            "embedding": [base, float(index + 1)],
                        }
                        for index in reversed(range(len(texts)))
                    ]
                },
                "usage": {"total_tokens": len(texts) * 10},
            }
        )

    monkeypatch.setattr("core.knowledge.embeddings.httpx.post", fake_post)
    provider = DashScopeEmbeddingProvider(
        DashScopeEmbeddingConfig(
            api_key="secret",
            base_url="https://workspace.example/api/v1",
            model="text-embedding-v4",
            model_revision="text-embedding-v4@2026-07-28",
            dimensions=2,
            query_instruct=(
                "Given a technical documentation query, retrieve relevant official documentation"
            ),
            batch_size=2,
            cost_per_1k_tokens_usd=0.0001,
        )
    )

    provider.prepare_documents(("same", "document-two"))
    document_vector = provider.embed_document("same")
    query_vector = provider.embed_query("same")

    assert document_vector == pytest.approx((3.0 / 10**0.5, 1.0 / 10**0.5))
    assert query_vector == pytest.approx((4.0 / 17**0.5, 1.0 / 17**0.5))
    assert len(calls) == 2
    assert calls[0]["json"]["parameters"] == {
        "dimension": 2,
        "text_type": "document",
        "output_type": "dense",
    }
    assert calls[1]["json"]["parameters"] == {
        "dimension": 2,
        "text_type": "query",
        "output_type": "dense",
        "instruct": (
            "Given a technical documentation query, retrieve relevant official documentation"
        ),
    }
    assert calls[0]["json"]["input"] == {"texts": ["same", "document-two"]}
    assert calls[1]["json"]["input"] == {"texts": ["same"]}
    assert provider.estimated_cost_usd == pytest.approx(0.000003)


def test_dashscope_provider_retries_transient_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> _Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.RemoteProtocolError("transient disconnect")
        return _Response({"output": {"embeddings": [{"text_index": 0, "embedding": [3.0, 4.0]}]}})

    monkeypatch.setattr("core.knowledge.embeddings.httpx.post", fake_post)
    provider = DashScopeEmbeddingProvider(
        DashScopeEmbeddingConfig(
            api_key="secret",
            base_url="https://workspace.example/api/v1",
            model="text-embedding-v4",
            model_revision="text-embedding-v4@2026-07-28",
            dimensions=2,
            max_attempts=3,
            retry_backoff_seconds=0.0,
        )
    )

    assert provider.embed_query("retry") == pytest.approx((0.6, 0.8))
    assert calls == 2


@pytest.mark.parametrize("status_code", (429, 503))
def test_dashscope_provider_retries_retryable_http_statuses(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    calls = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> _Response | httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            request = httpx.Request("POST", "https://workspace.example/api/v1")
            return httpx.Response(status_code, request=request)
        return _Response({"output": {"embeddings": [{"text_index": 0, "embedding": [3.0, 4.0]}]}})

    monkeypatch.setattr("core.knowledge.embeddings.httpx.post", fake_post)
    provider = DashScopeEmbeddingProvider(
        DashScopeEmbeddingConfig(
            api_key="secret",
            base_url="https://workspace.example/api/v1",
            model="text-embedding-v4",
            model_revision="text-embedding-v4@2026-07-28",
            dimensions=2,
            max_attempts=3,
            retry_backoff_seconds=0.0,
        )
    )

    assert provider.embed_query("retry-status") == pytest.approx((0.6, 0.8))
    assert calls == 2


def test_dashscope_provider_does_not_retry_authentication_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        nonlocal calls
        calls += 1
        request = httpx.Request("POST", "https://workspace.example/api/v1")
        return httpx.Response(401, request=request)

    monkeypatch.setattr("core.knowledge.embeddings.httpx.post", fake_post)
    provider = DashScopeEmbeddingProvider(
        DashScopeEmbeddingConfig(
            api_key="secret",
            base_url="https://workspace.example/api/v1",
            model="text-embedding-v4",
            model_revision="text-embedding-v4@2026-07-28",
            dimensions=2,
            max_attempts=3,
            retry_backoff_seconds=0.0,
        )
    )

    with pytest.raises(RuntimeError, match="DashScope embedding request failed"):
        provider.embed_query("do-not-retry")
    assert calls == 1


def test_dashscope_provider_rejects_invalid_response_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_post(*_args: Any, **_kwargs: Any) -> _Response:
        return _Response(
            {
                "output": {
                    "embeddings": [
                        {"text_index": 0, "embedding": [3.0, 4.0]},
                        {"text_index": 0, "embedding": [3.0, 4.0]},
                    ]
                }
            }
        )

    monkeypatch.setattr("core.knowledge.embeddings.httpx.post", fake_post)
    provider = DashScopeEmbeddingProvider(
        DashScopeEmbeddingConfig(
            api_key="secret",
            base_url="https://workspace.example/api/v1",
            model="text-embedding-v4",
            model_revision="text-embedding-v4@2026-07-28",
            dimensions=2,
        )
    )

    with pytest.raises(RuntimeError, match="DashScope embedding request failed"):
        provider.prepare_documents(("first", "second"))


def test_fastembed_provider_pins_snapshot_batches_normalizes_and_caches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    downloads: list[dict[str, Any]] = []
    model_calls: list[dict[str, Any]] = []
    embedded_batches: list[tuple[str, ...]] = []

    def snapshot_download(**kwargs: Any) -> str:
        downloads.append(kwargs)
        return str(tmp_path / "pinned-model")

    class FakeTextEmbedding:
        def __init__(self, **kwargs: Any) -> None:
            model_calls.append(kwargs)

        def embed(self, texts: tuple[str, ...], *, batch_size: int) -> list[list[float]]:
            embedded_batches.append(tuple(texts))
            assert batch_size == 2
            return [[3.0, 4.0] for _text in texts]

    modules = {
        "fastembed": SimpleNamespace(__version__="0.8.0", TextEmbedding=FakeTextEmbedding),
        "huggingface_hub": SimpleNamespace(snapshot_download=snapshot_download),
    }
    monkeypatch.setattr(
        "core.knowledge.embeddings.import_module",
        lambda name: modules[name],
    )
    revision = "a" * 40
    provider = FastEmbedEmbeddingProvider(
        FastEmbedEmbeddingConfig(
            model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            repository="qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
            model_revision=revision,
            dimensions=2,
            cache_dir=tmp_path / "cache",
            batch_size=2,
        )
    )

    provider.prepare(())
    assert downloads == []
    provider.prepare(("first", "second", "third", "first"))

    assert provider.embed("first") == pytest.approx((0.6, 0.8))
    assert provider.embed("third") == pytest.approx((0.6, 0.8))
    assert provider.model_revision == f"{revision}+{FASTEMBED_RUNTIME_REVISION}"
    assert downloads == [
        {
            "repo_id": "qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
            "revision": revision,
            "cache_dir": str(tmp_path / "cache"),
            "local_files_only": False,
        }
    ]
    assert model_calls == [
        {
            "model_name": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "cache_dir": str(tmp_path / "cache"),
            "specific_model_path": str(tmp_path / "pinned-model"),
        }
    ]
    assert embedded_batches == [("first", "second"), ("third",)]


def test_fastembed_provider_fails_explicitly_when_runtime_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def unavailable(_name: str) -> Any:
        raise ImportError("missing")

    monkeypatch.setattr("core.knowledge.embeddings.import_module", unavailable)
    provider = FastEmbedEmbeddingProvider(
        FastEmbedEmbeddingConfig(
            model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            repository="qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
            model_revision="b" * 40,
            dimensions=384,
            cache_dir=tmp_path,
        )
    )

    with pytest.raises(RuntimeError, match="FastEmbed initialization failed"):
        provider.embed("query")


@pytest.mark.parametrize("revision", ["latest", "abc123", "g" * 40])
def test_fastembed_config_requires_a_full_hex_snapshot_revision(
    revision: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="40-character hexadecimal"):
        FastEmbedEmbeddingConfig(
            model="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            repository="qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q",
            model_revision=revision,
            dimensions=384,
            cache_dir=tmp_path,
        )
