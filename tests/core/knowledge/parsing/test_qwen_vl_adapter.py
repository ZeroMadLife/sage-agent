"""Qwen3-VL page-raster fallback protocol tests."""

from __future__ import annotations

import json

import httpx
import pytest

from core.knowledge.parsing import ExternalAdapterError, ExternalParseProgress, ParseRequest
from core.knowledge.parsing.adapters import QwenVlAdapter, QwenVlConfig
from core.knowledge.parsing.adapters.qwen_vl import _render_pdf_pages


def _request(payload: bytes = b"%PDF-scan") -> ParseRequest:
    return ParseRequest(
        source_id="src_scan",
        relative_path="reports/visual.pdf",
        source_revision="sha256:visual",
        media_type="application/pdf",
        payload=payload,
    )


async def test_qwen_vl_rasterizes_each_page_without_exposing_key_in_result() -> None:
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-secret-key"
        payload = json.loads(request.content)
        requests.append(payload)
        page = len(requests)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": f"# Page {page}\n\nRecognized {page}."}}]},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = QwenVlAdapter(
        QwenVlConfig(api_key="test-secret-key", max_pages=2),
        client=client,
        rasterizer=lambda _payload, _limit: (b"jpeg-one", b"jpeg-two"),
    )
    events: list[ExternalParseProgress] = []

    async def report(event: ExternalParseProgress) -> None:
        events.append(event)

    document = await adapter.parse(_request(), progress=report)

    assert len(requests) == 2
    assert all(item["model"] == "qwen3-vl-flash" for item in requests)
    assert document.provenance.parser_id == "qwen3-vl"
    assert "Recognized 1" in document.rendered_markdown
    assert "test-secret-key" not in document.rendered_markdown
    assert [(event.completed_units, event.total_units) for event in events] == [
        (1, 2),
        (2, 2),
    ]
    await client.aclose()


async def test_qwen_vl_rate_limit_is_retryable() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(429)))
    adapter = QwenVlAdapter(
        QwenVlConfig(api_key="test-key"),
        client=client,
        rasterizer=lambda _payload, _limit: (b"jpeg",),
    )

    with pytest.raises(ExternalAdapterError) as captured:
        await adapter.parse(_request(), progress=_ignore)

    assert captured.value.code == "http_429"
    assert captured.value.retryable is True
    await client.aclose()


def test_default_pdf_rasterizer_enforces_page_limit() -> None:
    from io import BytesIO

    from pypdf import PdfWriter

    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    writer.write(buffer)

    with pytest.raises(ValueError, match="page_limit"):
        _render_pdf_pages(buffer.getvalue(), 1)


async def _ignore(_: ExternalParseProgress) -> None:
    return None
