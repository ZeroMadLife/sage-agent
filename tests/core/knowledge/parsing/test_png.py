"""PNG L1 parsing validates pixels and preserves a normalized visual region."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image, PngImagePlugin

from core.knowledge.parsing import DocumentParseError, ParseRequest, PngImageParser


def _png_payload(*, description: str = "Exact scan baseline") -> bytes:
    output = BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Title", "Retrieval latency")
    metadata.add_text("Description", description)
    Image.new("RGB", (320, 180), "white").save(output, format="PNG", pnginfo=metadata)
    return output.getvalue()


def test_png_parser_returns_searchable_metadata_and_whole_image_region() -> None:
    request = ParseRequest(
        source_id="src_png",
        relative_path="diagrams/retrieval-latency.png",
        source_revision="sha256:png",
        media_type="image/png",
        payload=_png_payload(),
    )

    document = PngImageParser().parse(request)

    assert document.title == "Retrieval latency"
    assert document.provenance.parser_id == "sage.png"
    assert len(document.blocks) == 1
    block = document.blocks[0]
    assert block.kind == "media"
    assert block.page == 1
    assert block.bbox == (0.0, 0.0, 1.0, 1.0)
    assert block.media_ref == "diagrams/retrieval-latency.png"
    assert block.confidence == 1.0
    assert "320 x 180 px" in block.text
    assert "Exact scan baseline" in block.text


def test_png_parser_rejects_truncated_or_spoofed_images() -> None:
    parser = PngImageParser()
    request = ParseRequest(
        source_id="src_png",
        relative_path="diagrams/spoofed.png",
        source_revision="sha256:bad",
        media_type="image/png",
        payload=b"not-a-png",
    )

    with pytest.raises(DocumentParseError, match="PNG source could not be parsed"):
        parser.parse(request)
