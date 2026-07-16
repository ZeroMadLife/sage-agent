"""Provider-neutral single-source loading for compatibility endpoints."""

from __future__ import annotations

from pathlib import PurePosixPath

from core.knowledge.store import KnowledgeSourceRoot

from .errors import KnowledgeScanError
from .registry import KnowledgeSourceAdapterRegistry
from .types import ImmutableSourceArtifact


async def fetch_source_by_key(
    registry: KnowledgeSourceAdapterRegistry,
    source: KnowledgeSourceRoot,
    source_key: str,
) -> ImmutableSourceArtifact:
    """Discover and fetch one key without accepting a client-supplied revision."""

    normalized = _source_key(source_key)
    scope = normalized.parent.as_posix()
    adapter = registry.resolve(source)
    cursor: str | None = None
    target_checkpoint: str | None = None
    while True:
        page = await adapter.scan(source, scope, None, cursor, 500)
        if target_checkpoint is None:
            target_checkpoint = page.target_checkpoint
        elif target_checkpoint != page.target_checkpoint:
            raise KnowledgeScanError("knowledge source changed during scan")
        for descriptor in page.items:
            if descriptor.source_key == normalized.as_posix():
                return await adapter.fetch(source, descriptor)
        if page.complete:
            raise FileNotFoundError("knowledge source not found")
        if page.next_cursor is None:
            raise KnowledgeScanError("incomplete source scan has no cursor")
        cursor = page.next_cursor


def _source_key(value: str) -> PurePosixPath:
    if "\\" in value:
        raise KnowledgeScanError("invalid relative source path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.name in {"", "."}:
        raise KnowledgeScanError("invalid relative source path")
    return path
