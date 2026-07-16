"""Provider-neutral contracts for knowledge source discovery and retrieval."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from core.knowledge.store import KnowledgeSourceRoot


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """Stable, browser-safe identity for one immutable source revision."""

    source_key: str
    source_revision: str
    media_type: str
    size_bytes: int
    modified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SourceScanPage:
    """One deterministic page from a source provider scan."""

    items: tuple[SourceDescriptor, ...]
    next_cursor: str | None
    target_checkpoint: str
    complete: bool


@dataclass(frozen=True, slots=True)
class ImmutableSourceArtifact:
    """Fetched bytes pinned to the descriptor revision used by the plan."""

    source_key: str
    source_revision: str
    media_type: str
    content: bytes
    metadata: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class KnowledgeSourceAdapter(Protocol):
    """Minimal connector contract shared by filesystem and remote providers."""

    adapter_id: str
    adapter_version: str
    supported_kinds: frozenset[str]

    async def scan(
        self,
        source: KnowledgeSourceRoot,
        scope: str,
        checkpoint: str | None,
        cursor: str | None,
        limit: int,
    ) -> SourceScanPage: ...

    async def fetch(
        self,
        source: KnowledgeSourceRoot,
        descriptor: SourceDescriptor,
    ) -> ImmutableSourceArtifact: ...

    async def acknowledge(
        self,
        source: KnowledgeSourceRoot,
        descriptor: SourceDescriptor,
        outcome: str,
    ) -> None: ...
