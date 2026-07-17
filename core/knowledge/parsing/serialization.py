"""Versioned JSON serialization for immutable parse artifacts."""

from __future__ import annotations

import json
from typing import Any, cast

from .types import BlockKind, ParsedBlock, ParsedDocument, ParseProvenance

_FORMAT_VERSION = 1


def serialize_document(document: ParsedDocument) -> str:
    payload = {
        "format_version": _FORMAT_VERSION,
        "document_id": document.document_id,
        "source_id": document.source_id,
        "relative_path": document.relative_path,
        "source_revision": document.source_revision,
        "title": document.title,
        "language": document.language,
        "rendered_markdown": document.rendered_markdown,
        "provenance": {
            "parser_id": document.provenance.parser_id,
            "parser_version": document.provenance.parser_version,
            "input_revision": document.provenance.input_revision,
            "media_type": document.provenance.media_type,
        },
        "blocks": [
            {
                "block_id": block.block_id,
                "ordinal": block.ordinal,
                "kind": block.kind,
                "text": block.text,
                "heading_path": list(block.heading_path),
                "page": block.page,
                "bbox": list(block.bbox) if block.bbox else None,
                "media_ref": block.media_ref,
                "confidence": block.confidence,
            }
            for block in document.blocks
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def deserialize_document(value: str) -> ParsedDocument:
    payload: Any = json.loads(value)
    if not isinstance(payload, dict) or payload.get("format_version") != _FORMAT_VERSION:
        raise ValueError("unsupported parse artifact format")
    provenance = payload["provenance"]
    blocks = payload["blocks"]
    if not isinstance(provenance, dict) or not isinstance(blocks, list):
        raise ValueError("invalid parse artifact")
    return ParsedDocument(
        document_id=str(payload["document_id"]),
        source_id=str(payload["source_id"]),
        relative_path=str(payload["relative_path"]),
        source_revision=str(payload["source_revision"]),
        title=str(payload["title"]),
        language=str(payload["language"]),
        rendered_markdown=str(payload["rendered_markdown"]),
        provenance=ParseProvenance(
            parser_id=str(provenance["parser_id"]),
            parser_version=str(provenance["parser_version"]),
            input_revision=str(provenance["input_revision"]),
            media_type=str(provenance["media_type"]),
        ),
        blocks=tuple(_deserialize_block(block) for block in blocks),
    )


def _deserialize_block(value: object) -> ParsedBlock:
    if not isinstance(value, dict):
        raise ValueError("invalid parse artifact block")
    raw_kind = str(value["kind"])
    allowed_kinds = {
        "frontmatter",
        "heading",
        "paragraph",
        "list",
        "code",
        "table",
        "quote",
        "media",
    }
    if raw_kind not in allowed_kinds:
        raise ValueError("invalid parse artifact block kind")
    raw_heading_path = value["heading_path"]
    if not isinstance(raw_heading_path, list):
        raise ValueError("invalid parse artifact heading path")
    raw_bbox = value.get("bbox")
    bbox: tuple[float, float, float, float] | None = None
    if raw_bbox is not None:
        if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
            raise ValueError("invalid parse artifact bounding box")
        bbox = (
            float(raw_bbox[0]),
            float(raw_bbox[1]),
            float(raw_bbox[2]),
            float(raw_bbox[3]),
        )
    return ParsedBlock(
        block_id=str(value["block_id"]),
        ordinal=int(value["ordinal"]),
        kind=cast(BlockKind, raw_kind),
        text=str(value["text"]),
        heading_path=tuple(str(part) for part in raw_heading_path),
        page=int(value["page"]) if value.get("page") is not None else None,
        bbox=bbox,
        media_ref=str(value["media_ref"]) if value.get("media_ref") else None,
        confidence=float(value["confidence"]),
    )
