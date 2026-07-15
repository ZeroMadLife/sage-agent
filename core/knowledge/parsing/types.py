"""Stable parser contracts shared by local and external knowledge adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BlockKind = Literal[
    "frontmatter",
    "heading",
    "paragraph",
    "list",
    "code",
    "table",
    "quote",
    "media",
]


@dataclass(frozen=True, slots=True)
class ParseRequest:
    """One immutable source revision presented to a parser."""

    source_id: str
    relative_path: str
    source_revision: str
    media_type: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class ParseProvenance:
    """Reproducibility metadata for one parser execution."""

    parser_id: str
    parser_version: str
    input_revision: str
    media_type: str


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    """One ordered semantic block with optional layout/media evidence."""

    block_id: str
    ordinal: int
    kind: BlockKind
    text: str
    heading_path: tuple[str, ...]
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    media_ref: str | None = None
    confidence: float = 1.0


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Canonical parser output consumed by synthesis and review stages."""

    document_id: str
    source_id: str
    relative_path: str
    source_revision: str
    title: str
    language: str
    rendered_markdown: str
    blocks: tuple[ParsedBlock, ...]
    provenance: ParseProvenance


@dataclass(frozen=True, slots=True)
class ParseArtifact:
    """Persisted parser result associated with one reviewable proposal."""

    artifact_id: str
    proposal_id: str
    document: ParsedDocument
    created_at: str
