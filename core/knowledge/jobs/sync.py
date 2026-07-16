"""Deterministic source-manifest diffing for incremental Knowledge sync."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Literal, cast

from .types import KnowledgeSyncChange, ScannedKnowledgeFile


def build_sync_changes(
    scanned: Sequence[ScannedKnowledgeFile],
    previous_manifest: Mapping[str, tuple[str, str]],
    *,
    workspace_id: str,
    source_root_id: str,
    relative_directory: str,
    pipeline_version: str,
) -> tuple[KnowledgeSyncChange, ...]:
    """Diff one bounded scan against the last committed manifest.

    ``previous_manifest`` values are ``(source_revision, status)``. Deleted
    entries stay in the manifest as tombstones and are therefore not emitted
    repeatedly until a source reappears with a new revision.
    """

    scope = _normalize_directory(relative_directory)
    prefix = "" if scope == "." else f"{scope}/"
    current = {item.relative_path: item for item in scanned}
    changes: list[KnowledgeSyncChange] = []

    for path in sorted(current):
        item = current[path]
        previous = previous_manifest.get(path)
        previous_revision = previous[0] if previous else None
        previous_status = previous[1] if previous else None
        if previous is not None and previous_status == "present":
            if previous_revision == item.source_revision:
                continue
            change_kind = "modified"
        else:
            change_kind = "added"
        changes.append(
            KnowledgeSyncChange(
                relative_path=path,
                change_kind=cast(Literal["added", "modified", "deleted"], change_kind),
                previous_revision=previous_revision,
                source_revision=item.source_revision,
                idempotency_key=_idempotency_key(
                    workspace_id,
                    source_root_id,
                    path,
                    change_kind,
                    item.source_revision,
                    pipeline_version,
                ),
            )
        )

    for path, (previous_revision, previous_status) in sorted(previous_manifest.items()):
        if previous_status != "present" or path in current or (prefix and not path.startswith(prefix)):
            continue
        changes.append(
            KnowledgeSyncChange(
                relative_path=path,
                change_kind="deleted",
                previous_revision=previous_revision,
                source_revision=None,
                idempotency_key=_idempotency_key(
                    workspace_id,
                    source_root_id,
                    path,
                    "deleted",
                    previous_revision,
                    pipeline_version,
                ),
            )
        )
    return tuple(sorted(changes, key=lambda item: item.relative_path))


def build_plan_id(
    *,
    workspace_id: str,
    source_root_id: str,
    relative_directory: str,
    pipeline_version: str,
    base_watermark: int,
    changes: Sequence[KnowledgeSyncChange],
) -> tuple[str, str]:
    """Return a stable plan id and immutable plan-content hash."""

    payload = [
        {
            "relative_path": item.relative_path,
            "change_kind": item.change_kind,
            "previous_revision": item.previous_revision,
            "source_revision": item.source_revision,
            "idempotency_key": item.idempotency_key,
        }
        for item in changes
    ]
    canonical = json.dumps(
        {
            "workspace_id": workspace_id,
            "source_root_id": source_root_id,
            "relative_directory": _normalize_directory(relative_directory),
            "pipeline_version": pipeline_version,
            "base_watermark": base_watermark,
            "changes": payload,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return f"ksync_{digest[:30]}", digest


def build_manifest_hash(
    previous_manifest: Mapping[str, tuple[str, str]],
    changes: Sequence[KnowledgeSyncChange],
) -> str:
    """Hash the complete manifest state produced by applying one sync plan."""

    target = dict(previous_manifest)
    for item in changes:
        if item.change_kind == "deleted":
            revision = item.previous_revision
            status = "deleted"
        else:
            revision = item.source_revision
            status = "present"
        if revision is None:
            raise ValueError(f"knowledge sync change has no revision: {item.relative_path}")
        target[item.relative_path] = (revision, status)
    canonical = json.dumps(
        [
            {
                "relative_path": path,
                "source_revision": revision,
                "status": status,
            }
            for path, (revision, status) in sorted(target.items())
        ],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _idempotency_key(
    workspace_id: str,
    source_root_id: str,
    relative_path: str,
    change_kind: str,
    revision: str,
    pipeline_version: str,
) -> str:
    parts: tuple[str, ...] = (
        workspace_id,
        source_root_id,
        relative_path,
        revision,
        pipeline_version,
    )
    if change_kind != "added":
        parts = (*parts[:3], change_kind, *parts[3:])
    material = "\0".join(parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _normalize_directory(value: str) -> str:
    normalized = value.strip() or "."
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or "\\" in normalized:
        raise ValueError("invalid relative source directory")
    return path.as_posix()
