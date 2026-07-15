"""Dependency-free, heading-aware Markdown parser."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from .common import decode_utf8, stable_id
from .types import BlockKind, ParsedBlock, ParsedDocument, ParseProvenance, ParseRequest

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"^(?:\s*[-+*]\s+|\s*\d+[.)]\s+)")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


class MarkdownParser:
    parser_id = "sage.markdown"
    parser_version = "1.0.0"
    priority = 100
    media_types = frozenset({"text/markdown", "text/x-markdown"})
    extensions = frozenset({".md", ".markdown"})

    def parse(self, request: ParseRequest) -> ParsedDocument:
        content = decode_utf8(request.payload, document_kind="Markdown")
        document_id = stable_id(
            "pdoc",
            request.source_id,
            request.relative_path,
            request.source_revision,
            self.parser_id,
            self.parser_version,
        )
        blocks = self._blocks(document_id, content)
        title = next(
            (
                block.text.removeprefix("# ").strip()
                for block in blocks
                if block.kind == "heading" and block.text.startswith("# ")
            ),
            PurePosixPath(request.relative_path).stem.replace("-", " ").replace("_", " "),
        )
        return ParsedDocument(
            document_id=document_id,
            source_id=request.source_id,
            relative_path=request.relative_path,
            source_revision=request.source_revision,
            title=title.strip() or "Untitled",
            language="und",
            rendered_markdown=content,
            blocks=tuple(blocks),
            provenance=ParseProvenance(
                parser_id=self.parser_id,
                parser_version=self.parser_version,
                input_revision=request.source_revision,
                media_type=request.media_type,
            ),
        )

    def _blocks(self, document_id: str, content: str) -> list[ParsedBlock]:
        lines = content.splitlines()
        result: list[ParsedBlock] = []
        headings: list[str] = []
        index = 0
        if lines and lines[0].strip() == "---":
            end = next(
                (position for position in range(1, len(lines)) if lines[position].strip() == "---"),
                None,
            )
            if end is not None:
                self._append(result, document_id, "frontmatter", lines[: end + 1], headings)
                index = end + 1

        while index < len(lines):
            if not lines[index].strip():
                index += 1
                continue
            heading = _HEADING.match(lines[index])
            if heading:
                level = len(heading.group(1))
                title = heading.group(2).strip()
                headings[:] = headings[: level - 1]
                headings.append(title)
                self._append(result, document_id, "heading", [lines[index]], headings)
                index += 1
                continue
            fence = _FENCE.match(lines[index])
            if fence:
                marker = fence.group(1)[0]
                start = index
                index += 1
                while index < len(lines):
                    if lines[index].lstrip().startswith(marker * 3):
                        index += 1
                        break
                    index += 1
                self._append(result, document_id, "code", lines[start:index], headings)
                continue
            if _LIST_ITEM.match(lines[index]):
                start = index
                index += 1
                while index < len(lines) and (
                    _LIST_ITEM.match(lines[index])
                    or (lines[index].startswith("  ") and lines[index].strip())
                ):
                    index += 1
                self._append(result, document_id, "list", lines[start:index], headings)
                continue
            kind: BlockKind = "quote" if lines[index].lstrip().startswith(">") else "paragraph"
            start = index
            index += 1
            while index < len(lines) and lines[index].strip():
                if (
                    _HEADING.match(lines[index])
                    or _FENCE.match(lines[index])
                    or _LIST_ITEM.match(lines[index])
                ):
                    break
                if kind == "quote" and not lines[index].lstrip().startswith(">"):
                    break
                index += 1
            self._append(result, document_id, kind, lines[start:index], headings)
        return result

    @staticmethod
    def _append(
        blocks: list[ParsedBlock],
        document_id: str,
        kind: BlockKind,
        lines: list[str],
        headings: list[str],
    ) -> None:
        text = "\n".join(lines).strip()
        ordinal = len(blocks)
        block_id = stable_id(
            "pblk",
            document_id,
            str(ordinal),
            kind,
            "\0".join(headings),
            stable_id("text", text),
        )
        blocks.append(
            ParsedBlock(
                block_id=block_id,
                ordinal=ordinal,
                kind=kind,
                text=text,
                heading_path=tuple(headings),
            )
        )
