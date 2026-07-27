"""Qwen3-VL page-raster fallback protocol tests."""

from __future__ import annotations

import json
from io import BytesIO

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


async def test_qwen_vl_preserves_normalized_regions_for_png_citations() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "title": "Retrieval chart",
                                    "regions": [
                                        {
                                            "kind": "table",
                                            "text": "exact P95 36 ms; cross encoder P95 1436 ms",
                                            "bbox": [0.1, 0.2, 0.9, 0.8],
                                            "confidence": 0.93,
                                        }
                                    ],
                                }
                            )
                        }
                    }
                ]
            },
        )

    png = _png_bytes()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = QwenVlAdapter(
        QwenVlConfig(api_key="test-key"),
        client=client,
    )
    request = ParseRequest(
        source_id="src_png",
        relative_path="charts/retrieval.png",
        source_revision="sha256:png",
        media_type="image/png",
        payload=png,
    )

    document = await adapter.parse(request, progress=_ignore)

    assert document.title == "Retrieval chart"
    assert document.provenance.parser_id == "qwen3-vl"
    assert document.provenance.parser_version == "2.0.0"
    assert len(document.blocks) == 1
    block = document.blocks[0]
    assert block.kind == "table"
    assert block.page == 1
    assert block.bbox == (0.1, 0.2, 0.9, 0.8)
    assert block.media_ref == "charts/retrieval.png"
    assert block.confidence == 0.93
    assert "cross encoder P95 1436 ms" in document.rendered_markdown
    await client.aclose()


async def test_qwen_vl_rejects_boolean_bbox_values() -> None:
    result = {
        "title": "Invalid region",
        "regions": [
            {
                "kind": "table",
                "text": "must not be accepted",
                "bbox": [True, 0.2, 0.9, 0.8],
                "confidence": 0.9,
            }
        ],
    }
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(result)}}]},
            )
        )
    )
    adapter = QwenVlAdapter(QwenVlConfig(api_key="test-key"), client=client)

    with pytest.raises(ExternalAdapterError) as captured:
        await adapter.parse(
            ParseRequest(
                source_id="src_png",
                relative_path="charts/invalid.png",
                source_revision="sha256:invalid",
                media_type="image/png",
                payload=_png_bytes(),
            ),
            progress=_ignore,
        )

    assert captured.value.code == "invalid_result"
    assert captured.value.retryable is False
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


def _png_bytes() -> bytes:
    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (64, 32), "white").save(output, format="PNG")
    return output.getvalue()
