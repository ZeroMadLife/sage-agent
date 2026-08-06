"""Deterministic UTF-8 plain-text parser for long-form books."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from .common import decode_utf8, stable_id
from .types import ParsedBlock, ParsedDocument, ParseProvenance, ParseRequest

_CHAPTER_HEADING = re.compile(
    r"^\s*(?:(?:chapter|part|book|volume)\s+[0-9ivxlcdm]+\b|"
    r"(?:第[一二三四五六七八九十百千万零〇○\d]+[章节回部卷篇]))(?:[\s:：.-].*)?\s*$",
    re.IGNORECASE,
)
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")


class TxtParser:
    """Parse TXT without inventing pages or losing source offsets."""

    parser_id = "sage.txt"
    parser_version = "1.0.0"
    priority = 90
    media_types = frozenset({"text/plain"})
    extensions = frozenset({".txt"})

    def parse(self, request: ParseRequest) -> ParsedDocument:
        decoded = decode_utf8(request.payload, document_kind="TXT")
        bom_bytes = 3 if decoded.startswith("\ufeff") else 0
        content = decoded[1:] if bom_bytes else decoded
        document_id = stable_id(
            "pdoc",
            request.source_id,
            request.relative_path,
            request.source_revision,
            self.parser_id,
            self.parser_version,
        )
        lines = _source_lines(content, bom_bytes=bom_bytes)
        blocks: list[ParsedBlock] = []
        heading_path: tuple[str, ...] = ()
        pending: list[_SourceLine] = []

        def flush_pending() -> None:
            if not pending:
                return
            first = pending[0]
            last = pending[-1]
            text = "\n".join(item.text for item in pending).strip()
            if text:
                ordinal = len(blocks)
                blocks.append(
                    _block(
                        document_id,
                        ordinal,
                        "paragraph",
                        text,
                        heading_path,
                        first,
                        last,
                    )
                )
            pending.clear()

        for line in lines:
            if not line.text.strip():
                flush_pending()
                continue
            if _CHAPTER_HEADING.match(line.text):
                flush_pending()
                heading = line.text.strip()
                heading_path = (heading,)
                blocks.append(
                    _block(
                        document_id,
                        len(blocks),
                        "heading",
                        heading,
                        heading_path,
                        line,
                        line,
                    )
                )
                continue
            pending.append(line)
        flush_pending()

        title = next(
            (
                block.text
                for block in blocks
                if block.kind == "paragraph" and not block.heading_path
            ),
            PurePosixPath(request.relative_path).stem.replace("-", " ").replace("_", " "),
        )
        return ParsedDocument(
            document_id=document_id,
            source_id=request.source_id,
            relative_path=request.relative_path,
            source_revision=request.source_revision,
            title=title.strip()[:500] or "Untitled",
            language=_language(content),
            rendered_markdown=content,
            blocks=tuple(blocks),
            provenance=ParseProvenance(
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                input_revision=request.source_revision,
                media_type=request.media_type,
            ),
        )


class _SourceLine:
    __slots__ = ("byte_end", "byte_start", "char_end", "char_start", "line", "text")

    def __init__(
        self,
        text: str,
        line: int,
        char_start: int,
        char_end: int,
        byte_start: int,
        byte_end: int,
    ) -> None:
        self.text = text
        self.line = line
        self.char_start = char_start
        self.char_end = char_end
        self.byte_start = byte_start
        self.byte_end = byte_end


def _source_lines(content: str, *, bom_bytes: int) -> tuple[_SourceLine, ...]:
    result: list[_SourceLine] = []
    char_cursor = 0
    byte_cursor = bom_bytes
    for line_number, raw_line in enumerate(content.splitlines(keepends=True), start=1):
        text = raw_line.rstrip("\r\n")
        char_end = char_cursor + len(text)
        byte_end = byte_cursor + len(text.encode("utf-8"))
        result.append(_SourceLine(text, line_number, char_cursor, char_end, byte_cursor, byte_end))
        char_cursor += len(raw_line)
        byte_cursor += len(raw_line.encode("utf-8"))
    if not result and content:
        result.append(
            _SourceLine(
                content,
                1,
                0,
                len(content),
                bom_bytes,
                bom_bytes + len(content.encode("utf-8")),
            )
        )
    return tuple(result)


def _block(
    document_id: str,
    ordinal: int,
    kind: str,
    text: str,
    heading_path: tuple[str, ...],
    first: _SourceLine,
    last: _SourceLine,
) -> ParsedBlock:
    block_id = stable_id(
        "pblk",
        document_id,
        str(ordinal),
        kind,
        "\0".join(heading_path),
        stable_id("text", text),
    )
    return ParsedBlock(
        block_id=block_id,
        ordinal=ordinal,
        kind=kind,  # type: ignore[arg-type]
        text=text,
        heading_path=heading_path,
        line_start=first.line,
        line_end=last.line,
        char_start=first.char_start,
        char_end=last.char_end,
        byte_start=first.byte_start,
        byte_end=last.byte_end,
    )


def _language(content: str) -> str:
    sample = content[:20_000]
    if not sample:
        return "und"
    cjk = len(_CJK.findall(sample))
    latin = len(_LATIN.findall(sample))
    if cjk and cjk / max(1, cjk + latin) >= 0.05:
        return "zh"
    if latin:
        return "en"
    return "und"
