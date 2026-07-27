"""Bounded DOCX parser preserving top-level paragraphs, tables, and media parts."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import PurePosixPath
from typing import Any

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from .common import stable_id
from .errors import DocumentParseError
from .types import BlockKind, ParsedBlock, ParsedDocument, ParseProvenance, ParseRequest

_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MAX_DOCX_BYTES = 20 * 1024 * 1024
_MAX_BLOCKS = 4_000
_MAX_TEXT_CHARACTERS = 4 * 1024 * 1024


class DocxParser:
    parser_id = "sage.docx"
    parser_version = "1.0.0"
    priority = 100
    media_types = frozenset({_DOCX_MEDIA_TYPE})
    extensions = frozenset({".docx"})

    def parse(self, request: ParseRequest) -> ParsedDocument:
        if not request.payload or len(request.payload) > _MAX_DOCX_BYTES:
            raise DocumentParseError("DOCX source exceeds input limits")
        try:
            document: Any = Document(BytesIO(request.payload))
        except Exception as exc:
            raise DocumentParseError("DOCX source could not be parsed") from exc
        document_id = stable_id(
            "pdoc",
            request.source_id,
            request.relative_path,
            request.source_revision,
            self.parser_id,
            self.parser_version,
        )
        records: list[tuple[BlockKind, str, tuple[str, ...], str | None]] = []
        headings: list[str] = []
        text_characters = 0
        try:
            for item in document.iter_inner_content():
                if isinstance(item, Paragraph):
                    text = _clean_text(item.text)
                    if not text:
                        continue
                    style_name = str(item.style.name or "") if item.style is not None else ""
                    heading_level = _heading_level(style_name)
                    if heading_level is not None:
                        headings[:] = headings[: heading_level - 1]
                        headings.append(text)
                        kind: BlockKind = "heading"
                        rendered = f"{'#' * heading_level} {text}"
                    elif style_name.casefold().startswith("list"):
                        kind = "list"
                        rendered = f"- {text}"
                    else:
                        kind = "paragraph"
                        rendered = text
                    records.append((kind, rendered, tuple(headings), None))
                    text_characters += len(rendered)
                elif isinstance(item, Table):
                    rendered = _render_table(item)
                    if rendered:
                        records.append(("table", rendered, tuple(headings), None))
                        text_characters += len(rendered)
                if len(records) > _MAX_BLOCKS or text_characters > _MAX_TEXT_CHARACTERS:
                    raise DocumentParseError("DOCX parsed content exceeds limits")
            for media_ref in _media_parts(request.payload):
                records.append(
                    (
                        "media",
                        f"Embedded image: {PurePosixPath(media_ref).name}",
                        tuple(headings),
                        media_ref,
                    )
                )
                if len(records) > _MAX_BLOCKS:
                    raise DocumentParseError("DOCX parsed content exceeds limits")
        except DocumentParseError:
            raise
        except Exception as exc:
            raise DocumentParseError("DOCX source could not be parsed") from exc
        title = _clean_text(str(document.core_properties.title or ""))
        if not title:
            title = next(
                (
                    text.removeprefix("# ").strip()
                    for kind, text, _headings, _media_ref in records
                    if kind == "heading" and text.startswith("# ")
                ),
                PurePosixPath(request.relative_path).stem.replace("-", " ").replace("_", " "),
            )
        blocks = tuple(
            ParsedBlock(
                block_id=stable_id(
                    "pblk",
                    document_id,
                    str(ordinal),
                    kind,
                    "\0".join(block_headings),
                    text,
                    media_ref or "",
                ),
                ordinal=ordinal,
                kind=kind,
                text=text,
                heading_path=block_headings,
                media_ref=media_ref,
            )
            for ordinal, (kind, text, block_headings, media_ref) in enumerate(records)
        )
        rendered = "\n\n".join(block.text for block in blocks).strip()
        return ParsedDocument(
            document_id=document_id,
            source_id=request.source_id,
            relative_path=request.relative_path,
            source_revision=request.source_revision,
            title=title[:500] or "Untitled",
            language="und",
            rendered_markdown=(rendered + "\n") if rendered else "",
            blocks=blocks,
            provenance=ParseProvenance(
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                input_revision=request.source_revision,
                media_type=request.media_type,
            ),
        )


def _heading_level(style_name: str) -> int | None:
    match = re.fullmatch(r"Heading\s+([1-6])", style_name.strip(), flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _render_table(table: Table) -> str:
    rows = [[_clean_text(cell.text) for cell in row.cells] for row in table.rows]
    if not rows or not any(any(cell for cell in row) for row in rows):
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(_escape_cell(cell) for cell in normalized[0]) + " |"]
    lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    lines.extend(
        "| " + " | ".join(_escape_cell(cell) for cell in row) + " |" for row in normalized[1:]
    )
    return "\n".join(lines)


def _media_parts(payload: bytes) -> tuple[str, ...]:
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        refs = {
            name
            for name in archive.namelist()
            if name.startswith("word/media/") and not name.endswith("/")
        }
    return tuple(sorted(refs))


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")
