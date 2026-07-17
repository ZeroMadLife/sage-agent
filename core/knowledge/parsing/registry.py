"""Deterministic registry for built-in and optional document parsers."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Protocol

from .types import ParsedDocument, ParseRequest


class ParserNotFoundError(LookupError):
    """No registered parser can safely handle the source."""


class ParserConflictError(RuntimeError):
    """Parser registration or routing is ambiguous."""


class DocumentParser(Protocol):
    parser_id: str
    parser_version: str
    priority: int
    media_types: frozenset[str]
    extensions: frozenset[str]

    def parse(self, request: ParseRequest) -> ParsedDocument:
        """Parse one immutable source revision."""


class ParserRegistry:
    """Resolve exactly one parser using stable priority and identity rules."""

    def __init__(self) -> None:
        self._parsers: dict[str, DocumentParser] = {}

    def register(self, parser: DocumentParser) -> None:
        parser_id = parser.parser_id.strip()
        if not parser_id or parser_id in self._parsers:
            raise ParserConflictError(f"parser {parser_id or '<empty>'} is already registered")
        self._parsers[parser_id] = parser

    def resolve(self, request: ParseRequest) -> DocumentParser:
        extension = PurePosixPath(request.relative_path).suffix.lower()
        candidates = [
            parser
            for parser in self._parsers.values()
            if request.media_type.lower() in parser.media_types or extension in parser.extensions
        ]
        if not candidates:
            raise ParserNotFoundError(f"no parser for {request.media_type or 'unknown media type'}")
        candidates.sort(key=lambda parser: (-parser.priority, parser.parser_id))
        if len(candidates) > 1 and candidates[0].priority == candidates[1].priority:
            raise ParserConflictError(
                "ambiguous parsers: "
                + ", ".join(
                    parser.parser_id
                    for parser in candidates
                    if parser.priority == candidates[0].priority
                )
            )
        return candidates[0]

    def parse(self, request: ParseRequest) -> ParsedDocument:
        return self.resolve(request).parse(request)

    def parser_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._parsers))
