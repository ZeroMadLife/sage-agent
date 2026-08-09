from __future__ import annotations

from typing import Any

import httpx
import pytest

from scripts.benchmark_providers import doubao_multimodal, qwen3_vl


class _Response:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("POST", "https://embedding.example")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "request failed",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self) -> dict[str, Any]:
        return self._payload


class _Client:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: list[_Response | Exception] = []

    def post(self, _url: str, *, json: dict[str, Any]) -> _Response:
        self.calls.append(json)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_doubao_keeps_one_text_per_fused_request_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOUBAO_EMBEDDING_KEY", "test-only")
    monkeypatch.setenv("SAGE_DOUBAO_EMBEDDING_MAX_WORKERS", "1")
    client = _Client()
    client.responses = [
        _Response(
            {
                "data": {"embedding": [3.0] * 2048},
                "usage": {"prompt_tokens": 7},
            }
        ),
        _Response(
            {
                "data": {"embedding": [4.0] * 2048},
                "usage": {"prompt_tokens": 11},
            }
        ),
    ]
    monkeypatch.setattr(doubao_multimodal.httpx, "Client", lambda **_kwargs: client)

    provider = doubao_multimodal.DoubaoMultimodalEmbeddingProvider()
    provider.prepare(("first", "second", "first"))

    assert [call["input"] for call in client.calls] == [
        [{"type": "text", "text": "first"}],
        [{"type": "text", "text": "second"}],
    ]
    assert provider.embed("first") == pytest.approx((1 / 2048**0.5,) * 2048)
    assert provider.request_count == 2
    assert provider.input_tokens == 18


def test_doubao_retries_transient_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOUBAO_EMBEDDING_KEY", "test-only")
    client = _Client()
    client.responses = [
        _Response({}, status_code=429),
        _Response({"data": {"embedding": [1.0] * 2048}}),
    ]
    monkeypatch.setattr(doubao_multimodal.httpx, "Client", lambda **_kwargs: client)
    monkeypatch.setattr(doubao_multimodal.time, "sleep", lambda _seconds: None)

    doubao_multimodal.DoubaoMultimodalEmbeddingProvider().embed("retry")

    assert len(client.calls) == 2


def test_qwen_uses_non_fused_batches_and_separates_query_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BAILIAN_EMBEDDING_KEY", "test-only")
    monkeypatch.setenv("SAGE_QWEN3_VL_BATCH_SIZE", "2")
    client = _Client()
    client.responses = [
        _Response(
            {
                "output": {
                    "embeddings": [
                        {"index": 1, "embedding": [4.0] * 1024},
                        {"index": 0, "embedding": [3.0] * 1024},
                    ]
                },
                "usage": {"input_tokens": 13},
            }
        ),
        _Response(
            {
                "output": {"embeddings": [{"index": 0, "embedding": [5.0] * 1024}]},
                "usage": {"input_tokens": 5},
            }
        ),
    ]
    monkeypatch.setattr(qwen3_vl.httpx, "Client", lambda **_kwargs: client)

    provider = qwen3_vl.Qwen3VLEmbeddingProvider()
    provider.prepare_documents(("first", "second"))
    provider.embed_query("first")

    assert client.calls[0]["input"] == {"contents": [{"text": "first"}, {"text": "second"}]}
    assert client.calls[0]["parameters"]["enable_fusion"] is False
    assert "instruct" not in client.calls[0]["parameters"]
    assert client.calls[1]["parameters"]["enable_fusion"] is False
    assert client.calls[1]["parameters"]["instruct"] == qwen3_vl._QUERY_INSTRUCT
    assert provider.request_count == 2
    assert provider.input_tokens == 18


def test_qwen_rejects_fused_or_missing_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BAILIAN_EMBEDDING_KEY", "test-only")
    client = _Client()
    client.responses = [
        _Response({"output": {"embeddings": [{"index": 0, "embedding": [1.0] * 1024}]}})
    ]
    monkeypatch.setattr(qwen3_vl.httpx, "Client", lambda **_kwargs: client)

    provider = qwen3_vl.Qwen3VLEmbeddingProvider()
    with pytest.raises(RuntimeError, match="response is invalid"):
        provider.prepare_documents(("first", "second"))
