"""Compatibility wrappers around the provider-neutral source adapter layer."""

from __future__ import annotations

import hashlib

from core.knowledge import KnowledgeStore
from core.knowledge.sources import (
    KnowledgeScanError as KnowledgeScanError,
)
from core.knowledge.sources import (
    SourceDescriptor,
    read_filesystem_revision,
    scan_filesystem_descriptors,
)

from .types import ScannedKnowledgeFile


def scan_knowledge_directory(
    store: KnowledgeStore,
    source_root_id: str,
    relative_directory: str,
    *,
    workspace_id: str,
    pipeline_version: str,
    max_files: int = 10_000,
) -> list[ScannedKnowledgeFile]:
    """Return stable source revisions without exposing absolute server paths."""
    source = store.source_roots.get(source_root_id)
    if source is None:
        raise KeyError(source_root_id)
    descriptors = scan_filesystem_descriptors(
        source,
        relative_directory,
        max_files=max_files,
    )
    return [
        _scanned_file(
            descriptor,
            workspace_id=workspace_id,
            source_root_id=source_root_id,
            pipeline_version=pipeline_version,
        )
        for descriptor in descriptors
    ]


def scan_markdown_directory(
    store: KnowledgeStore,
    source_root_id: str,
    relative_directory: str,
    *,
    workspace_id: str,
    pipeline_version: str,
    max_files: int = 10_000,
) -> list[ScannedKnowledgeFile]:
    """Backward-compatible alias for callers created before P2.2-B2."""

    return scan_knowledge_directory(
        store,
        source_root_id,
        relative_directory,
        workspace_id=workspace_id,
        pipeline_version=pipeline_version,
        max_files=max_files,
    )


def read_source_revision(store: KnowledgeStore, source_root_id: str, relative_path: str) -> str:
    """Revalidate the exact source revision immediately before processing."""
    source = store.source_roots.get(source_root_id)
    if source is None:
        raise KeyError(source_root_id)
    return read_filesystem_revision(source, relative_path)


def _scanned_file(
    descriptor: SourceDescriptor,
    *,
    workspace_id: str,
    source_root_id: str,
    pipeline_version: str,
) -> ScannedKnowledgeFile:
    key_material = (
        f"{workspace_id}\0{source_root_id}\0{descriptor.source_key}\0"
        f"{descriptor.source_revision}\0{pipeline_version}"
    )
    return ScannedKnowledgeFile(
        relative_path=descriptor.source_key,
        source_revision=descriptor.source_revision,
        idempotency_key=hashlib.sha256(key_material.encode()).hexdigest(),
    )
