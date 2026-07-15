"""Bounded, symlink-safe knowledge source directory scanning."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath

from core.knowledge import KnowledgeStore

from .types import ScannedKnowledgeFile

_READ_CHUNK_BYTES = 1024 * 1024
_SUPPORTED_EXTENSIONS = frozenset({".md", ".markdown", ".html", ".htm", ".xhtml", ".pdf"})
_MAX_SCAN_FILE_BYTES = 20 * 1024 * 1024


class KnowledgeScanError(ValueError):
    """The requested scan would leave its configured trust boundary."""


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
    root = store.source_roots.get(source_root_id)
    if root is None:
        raise KeyError(source_root_id)
    relative = _relative_directory(relative_directory)
    source_root = root.path.resolve()
    unresolved_scan_root = source_root.joinpath(*relative.parts)
    if _has_symlink_component(source_root, relative):
        raise KnowledgeScanError("invalid relative source directory")
    scan_root = unresolved_scan_root.resolve()
    if not scan_root.is_relative_to(source_root):
        raise KnowledgeScanError("invalid relative source directory")
    if not scan_root.is_dir() or scan_root.is_symlink():
        raise FileNotFoundError("knowledge source directory not found")

    files: list[ScannedKnowledgeFile] = []
    for directory, dirnames, filenames in os.walk(scan_root, followlinks=False):
        current = Path(directory)
        dirnames[:] = sorted(name for name in dirnames if not (current / name).is_symlink())
        for filename in sorted(filenames):
            path = current / filename
            if (
                path.suffix.lower() not in _SUPPORTED_EXTENSIONS
                or path.is_symlink()
                or not path.is_file()
            ):
                continue
            if path.stat().st_size > _MAX_SCAN_FILE_BYTES:
                raise KnowledgeScanError("source file exceeds 20 MiB scan limit")
            source_relative = path.relative_to(source_root).as_posix()
            digest = _file_digest(path)
            source_revision = f"sha256:{digest}"
            key_material = (
                f"{workspace_id}\0{source_root_id}\0{source_relative}\0"
                f"{source_revision}\0{pipeline_version}"
            )
            files.append(
                ScannedKnowledgeFile(
                    relative_path=source_relative,
                    source_revision=source_revision,
                    idempotency_key=hashlib.sha256(key_material.encode()).hexdigest(),
                )
            )
            if len(files) > max_files:
                raise KnowledgeScanError(f"source directory exceeds {max_files} files")
    files.sort(key=lambda item: item.relative_path)
    return files


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
    root = store.source_roots.get(source_root_id)
    if root is None:
        raise KeyError(source_root_id)
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "\\" in relative_path
        or relative.suffix.lower() not in _SUPPORTED_EXTENSIONS
    ):
        raise KnowledgeScanError("invalid relative source path")
    source_root = root.path.resolve()
    path = source_root.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source_root):
        raise FileNotFoundError("knowledge source file not found")
    return f"sha256:{_file_digest(path)}"


def _relative_directory(value: str) -> PurePosixPath:
    normalized = value.strip() or "."
    if "\\" in normalized:
        raise KnowledgeScanError("invalid relative source directory")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        raise KnowledgeScanError("invalid relative source directory")
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
