"""Bounded, symlink-safe filesystem source adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from core.knowledge.store import KnowledgeSourceRoot

from .errors import KnowledgeScanError, KnowledgeSourceCheckpointConflictError
from .types import ImmutableSourceArtifact, SourceDescriptor, SourceScanPage

_READ_CHUNK_BYTES = 1024 * 1024
_MAX_SCAN_FILE_BYTES = 20 * 1024 * 1024
_MEDIA_TYPES = {
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".xhtml": "application/xhtml+xml",
    ".pdf": "application/pdf",
}


class FilesystemKnowledgeSourceAdapter:
    """Discover and fetch immutable revisions from a configured local root."""

    adapter_id = "filesystem"
    adapter_version = "1"
    supported_kinds = frozenset({"markdown", "obsidian"})

    async def scan(
        self,
        source: KnowledgeSourceRoot,
        scope: str,
        checkpoint: str | None,
        cursor: str | None,
        limit: int,
    ) -> SourceScanPage:
        del checkpoint
        return await asyncio.to_thread(_scan_page, source, scope, cursor, limit)

    async def fetch(
        self,
        source: KnowledgeSourceRoot,
        descriptor: SourceDescriptor,
    ) -> ImmutableSourceArtifact:
        return await asyncio.to_thread(_fetch_artifact, source, descriptor)

    async def acknowledge(
        self,
        source: KnowledgeSourceRoot,
        descriptor: SourceDescriptor,
        outcome: str,
    ) -> None:
        del source, descriptor, outcome


def scan_filesystem_descriptors(
    source: KnowledgeSourceRoot,
    scope: str,
    *,
    max_files: int = 10_000,
) -> tuple[SourceDescriptor, ...]:
    """Synchronous compatibility helper used by the legacy scanner API."""

    descriptors = _scan_descriptors(source, scope, max_files=max_files)
    return tuple(descriptors)


def fetch_filesystem_artifact(
    source: KnowledgeSourceRoot,
    descriptor: SourceDescriptor,
) -> ImmutableSourceArtifact:
    """Synchronous compatibility helper for exact revision reads."""

    return _fetch_artifact(source, descriptor)


def read_filesystem_revision(source: KnowledgeSourceRoot, source_key: str) -> str:
    """Read one exact revision without scanning unrelated source files."""

    relative = _relative_source_path(source_key)
    source_root = source.path.resolve()
    path = source_root.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source_root):
        raise FileNotFoundError("knowledge source file not found")
    return f"sha256:{_file_digest(path)}"


def descriptor_checkpoint(items: tuple[SourceDescriptor, ...] | list[SourceDescriptor]) -> str:
    payload = json.dumps(
        [(item.source_key, item.source_revision) for item in items],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _scan_page(
    source: KnowledgeSourceRoot,
    scope: str,
    cursor: str | None,
    limit: int,
) -> SourceScanPage:
    if limit < 1 or limit > 10_000:
        raise KnowledgeScanError("source scan limit must be between 1 and 10000")
    descriptors = _scan_descriptors(source, scope, max_files=10_000)
    checkpoint = descriptor_checkpoint(descriptors)
    offset = _decode_cursor(cursor, checkpoint)
    page = tuple(descriptors[offset : offset + limit])
    next_offset = offset + len(page)
    complete = next_offset >= len(descriptors)
    next_cursor = None if complete else _encode_cursor(checkpoint, next_offset)
    return SourceScanPage(
        items=page,
        next_cursor=next_cursor,
        target_checkpoint=checkpoint,
        complete=complete,
    )


def _scan_descriptors(
    source: KnowledgeSourceRoot,
    scope: str,
    *,
    max_files: int,
) -> list[SourceDescriptor]:
    relative = _relative_directory(scope)
    source_root = source.path.resolve()
    unresolved_scan_root = source_root.joinpath(*relative.parts)
    if _has_symlink_component(source_root, relative):
        raise KnowledgeScanError("invalid relative source directory")
    scan_root = unresolved_scan_root.resolve()
    if not scan_root.is_relative_to(source_root):
        raise KnowledgeScanError("invalid relative source directory")
    if not scan_root.is_dir() or scan_root.is_symlink():
        raise FileNotFoundError("knowledge source directory not found")

    items: list[SourceDescriptor] = []
    for directory, dirnames, filenames in os.walk(scan_root, followlinks=False):
        current = Path(directory)
        dirnames[:] = sorted(name for name in dirnames if not (current / name).is_symlink())
        for filename in sorted(filenames):
            path = current / filename
            media_type = _MEDIA_TYPES.get(path.suffix.lower())
            if media_type is None or path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            if stat.st_size > _MAX_SCAN_FILE_BYTES:
                raise KnowledgeScanError("source file exceeds 20 MiB scan limit")
            source_key = path.relative_to(source_root).as_posix()
            items.append(
                SourceDescriptor(
                    source_key=source_key,
                    source_revision=f"sha256:{_file_digest(path)}",
                    media_type=media_type,
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                )
            )
            if len(items) > max_files:
                raise KnowledgeScanError(f"source directory exceeds {max_files} files")
    items.sort(key=lambda item: item.source_key)
    return items


def _fetch_artifact(
    source: KnowledgeSourceRoot,
    descriptor: SourceDescriptor,
) -> ImmutableSourceArtifact:
    relative = _relative_source_path(descriptor.source_key)
    media_type = _MEDIA_TYPES[relative.suffix.lower()]
    source_root = source.path.resolve()
    path = source_root.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source_root):
        raise FileNotFoundError("knowledge source file not found")
    content = _read_bytes(path)
    revision = "sha256:" + hashlib.sha256(content).hexdigest()
    if revision != descriptor.source_revision:
        raise KnowledgeSourceCheckpointConflictError(
            "knowledge source revision changed during fetch"
        )
    return ImmutableSourceArtifact(
        source_key=descriptor.source_key,
        source_revision=revision,
        media_type=media_type,
        content=content,
        metadata={"adapter_id": "filesystem", "adapter_version": "1"},
    )


def _relative_directory(value: str) -> PurePosixPath:
    normalized = value.strip() or "."
    if "\\" in normalized:
        raise KnowledgeScanError("invalid relative source directory")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise KnowledgeScanError("invalid relative source directory")
    return path


def _relative_source_path(value: str) -> PurePosixPath:
    if "\\" in value:
        raise KnowledgeScanError("invalid relative source path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.suffix.lower() not in _MEDIA_TYPES
    ):
        raise KnowledgeScanError("invalid relative source path")
    return path


def _has_symlink_component(source_root: Path, relative: PurePosixPath) -> bool:
    current = source_root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        while chunk := source.read(_READ_CHUNK_BYTES):
            total += len(chunk)
            if total > _MAX_SCAN_FILE_BYTES:
                raise KnowledgeScanError("source file exceeds 20 MiB scan limit")
            digest.update(chunk)
    return digest.hexdigest()


def _read_bytes(path: Path) -> bytes:
    payload = path.read_bytes()
    if len(payload) > _MAX_SCAN_FILE_BYTES:
        raise KnowledgeScanError("source file exceeds 20 MiB scan limit")
    return payload


def _encode_cursor(checkpoint: str, offset: int) -> str:
    return f"v1:{checkpoint.removeprefix('sha256:')}:{offset}"


def _decode_cursor(cursor: str | None, checkpoint: str) -> int:
    if cursor is None:
        return 0
    parts = cursor.split(":")
    if len(parts) != 3 or parts[0] != "v1" or not parts[2].isdigit():
        raise KnowledgeScanError("invalid knowledge source scan cursor")
    if f"sha256:{parts[1]}" != checkpoint:
        raise KnowledgeSourceCheckpointConflictError(
            "knowledge source changed during paged scan"
        )
    return int(parts[2])
