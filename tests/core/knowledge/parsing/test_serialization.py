"""Parse artifact compatibility and locator persistence."""

import json

from core.knowledge.parsing import (
    ParsedBlock,
    ParsedDocument,
    ParseProvenance,
    deserialize_document,
    serialize_document,
)


def test_parse_artifact_round_trip_keeps_txt_locators() -> None:
    document = ParsedDocument(
        document_id="pdoc_book",
        source_id="book",
        relative_path="book.txt",
        source_revision="sha256:book",
        title="Book",
        language="zh",
        rendered_markdown="第一章\n\n正文\n",
        blocks=(
            ParsedBlock(
                block_id="pblk_body",
                ordinal=0,
                kind="paragraph",
                text="正文",
                heading_path=("第一章",),
                line_start=3,
                line_end=3,
                char_start=4,
                char_end=6,
                byte_start=10,
                byte_end=16,
            ),
        ),
        provenance=ParseProvenance("sage.txt", "1.0.0", "sha256:book", "text/plain"),
    )

    assert deserialize_document(serialize_document(document)) == document


def test_parse_artifact_format_v1_remains_readable() -> None:
    payload = {
        "format_version": 1,
        "document_id": "pdoc_legacy",
        "source_id": "source",
        "relative_path": "legacy.md",
        "source_revision": "sha256:legacy",
        "title": "Legacy",
        "language": "und",
        "rendered_markdown": "# Legacy\n",
        "provenance": {
            "parser_id": "sage.markdown",
            "parser_version": "1.0.0",
            "input_revision": "sha256:legacy",
            "media_type": "text/markdown",
        },
        "blocks": [
            {
                "block_id": "pblk_heading",
                "ordinal": 0,
                "kind": "heading",
                "text": "# Legacy",
                "heading_path": ["Legacy"],
                "page": None,
                "bbox": None,
                "media_ref": None,
                "confidence": 1.0,
            }
        ],
    }

    document = deserialize_document(json.dumps(payload))

    assert document.blocks[0].line_start is None
    assert document.provenance.parser_id == "sage.markdown"
