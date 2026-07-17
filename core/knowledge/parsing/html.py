"""Dependency-free HTML sanitizer and semantic parser."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import PurePosixPath

from .common import decode_utf8, stable_id
from .errors import DocumentParseError
from .types import BlockKind, ParsedBlock, ParsedDocument, ParseProvenance, ParseRequest

_SKIPPED_TAGS = frozenset({"script", "style", "noscript", "template", "svg", "canvas"})
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


class HtmlParser:
    parser_id = "sage.html"
    parser_version = "1.0.0"
    priority = 100
    media_types = frozenset({"text/html", "application/xhtml+xml"})
    extensions = frozenset({".html", ".htm", ".xhtml"})

    def parse(self, request: ParseRequest) -> ParsedDocument:
        content = decode_utf8(request.payload, document_kind="HTML")
        collector = _SemanticHtmlCollector()
        try:
            collector.feed(content)
            collector.close()
        except (AssertionError, ValueError) as exc:
            raise DocumentParseError("HTML source could not be parsed") from exc
        document_id = stable_id(
            "pdoc",
            request.source_id,
            request.relative_path,
            request.source_revision,
            self.parser_id,
            self.parser_version,
        )
        blocks = tuple(
            ParsedBlock(
                block_id=stable_id(
                    "pblk", document_id, str(index), kind, "\0".join(headings), text
                ),
                ordinal=index,
                kind=kind,
                text=text,
                heading_path=headings,
            )
            for index, (kind, text, headings) in enumerate(collector.records)
        )
        rendered = "\n\n".join(block.text for block in blocks).strip()
        if rendered:
            rendered += "\n"
        fallback = PurePosixPath(request.relative_path).stem.replace("-", " ").replace("_", " ")
        return ParsedDocument(
            document_id=document_id,
            source_id=request.source_id,
            relative_path=request.relative_path,
            source_revision=request.source_revision,
            title=collector.title.strip() or collector.first_heading or fallback or "Untitled",
            language="und",
            rendered_markdown=rendered,
            blocks=blocks,
            provenance=ParseProvenance(
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                input_revision=request.source_revision,
                media_type=request.media_type,
            ),
        )


class _SemanticHtmlCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.records: list[tuple[BlockKind, str, tuple[str, ...]]] = []
        self.title = ""
        self.first_heading = ""
        self._headings: list[str] = []
        self._skip_depth = 0
        self._capture_tag: str | None = None
        self._capture: list[str] = []
        self._title_depth = 0
        self._title_parts: list[str] = []
        self._row_cells: list[str] | None = None
        self._cell_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        tag = tag.lower()
        if tag in _SKIPPED_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._title_depth += 1
            return
        if tag == "tr":
            self._row_cells = []
            return
        if tag in {"td", "th"} and self._row_cells is not None:
            self._cell_parts = []
            return
        if self._capture_tag is None and (tag in _HEADING_TAGS or tag in {"p", "li", "pre"}):
            self._capture_tag = tag
            self._capture = []
        elif self._capture_tag == "p" and tag == "br":
            self._capture.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIPPED_TAGS:
            self._skip_depth = max(self._skip_depth - 1, 0)
            return
        if self._skip_depth:
            return
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
            if self._title_depth == 0:
                self.title = _clean_text(" ".join(self._title_parts))
            return
        if tag in {"td", "th"} and self._cell_parts is not None:
            if self._row_cells is not None:
                self._row_cells.append(_clean_text(" ".join(self._cell_parts)))
            self._cell_parts = None
            return
        if tag == "tr" and self._row_cells is not None:
            cells = [cell for cell in self._row_cells if cell]
            if cells:
                self._append("table", "| " + " | ".join(cells) + " |")
            self._row_cells = None
            return
        if self._capture_tag != tag:
            return
        text = _clean_text("".join(self._capture), preserve_lines=tag == "pre")
        self._capture_tag = None
        self._capture = []
        if not text:
            return
        if tag in _HEADING_TAGS:
            level = int(tag[1])
            self._headings[:] = self._headings[: level - 1]
            self._headings.append(text)
            if not self.first_heading:
                self.first_heading = text
            self._append("heading", f"{'#' * level} {text}")
        elif tag == "li":
            self._append("list", f"- {text}")
        elif tag == "pre":
            self._append("code", f"```\n{text}\n```")
        else:
            self._append("paragraph", text)

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._title_depth:
            self._title_parts.append(data)
        if self._cell_parts is not None:
            self._cell_parts.append(data)
        elif self._capture_tag is not None:
            self._capture.append(data)

    def _append(self, kind: BlockKind, text: str) -> None:
        headings = tuple(self._headings)
        if kind in {"list", "table"} and self.records:
            previous_kind, previous_text, previous_headings = self.records[-1]
            if previous_kind == kind and previous_headings == headings:
                self.records[-1] = (kind, f"{previous_text}\n{text}", headings)
                return
        self.records.append((kind, text, headings))


def _clean_text(value: str, *, preserve_lines: bool = False) -> str:
    if preserve_lines:
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
        return "\n".join(line for line in lines if line)
    return re.sub(r"\s+", " ", value).strip()
