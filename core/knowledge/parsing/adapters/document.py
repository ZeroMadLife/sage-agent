"""Convert bounded external Markdown into the canonical parsed-document contract."""

from __future__ import annotations

from dataclasses import replace

from ..common import stable_id
from ..markdown import MarkdownParser
from ..types import ParsedDocument, ParseProvenance, ParseRequest

_MAX_EXTERNAL_MARKDOWN_BYTES = 4 * 1024 * 1024


def external_markdown_document(
    request: ParseRequest,
    markdown: str,
    *,
    parser_id: str,
    parser_version: str,
    title: str | None = None,
    language: str = "und",
    confidence: float = 0.85,
) -> ParsedDocument:
    """Reuse deterministic Markdown block semantics with external provenance."""
    normalized = markdown.strip()
    if not normalized:
        raise ValueError("external parser returned empty Markdown")
    if len(normalized.encode("utf-8")) > _MAX_EXTERNAL_MARKDOWN_BYTES:
        raise ValueError("external parser Markdown exceeds 4 MiB limit")
    base = MarkdownParser().parse(replace(request, payload=(normalized + "\n").encode("utf-8")))
    document_id = stable_id(
        "pdoc",
        request.source_id,
        request.relative_path,
        request.source_revision,
        parser_id,
        parser_version,
        stable_id("output", normalized),
    )
    blocks = tuple(
        replace(
            block,
            block_id=stable_id(
                "pblk",
                document_id,
                str(block.ordinal),
                block.kind,
                block.text,
            ),
            confidence=min(block.confidence, confidence),
        )
        for block in base.blocks
    )
    return replace(
        base,
        document_id=document_id,
        title=(title or base.title).strip()[:500] or "Untitled",
        language=language,
        rendered_markdown=normalized + "\n",
        blocks=blocks,
        provenance=ParseProvenance(
            parser_id=parser_id,
            parser_version=parser_version,
            input_revision=request.source_revision,
            media_type=request.media_type,
        ),
    )
