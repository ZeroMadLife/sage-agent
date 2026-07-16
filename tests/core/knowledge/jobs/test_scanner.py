"""Safe directory scanner coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.knowledge import KnowledgeStore
from core.knowledge.jobs import (
    KnowledgeScanError,
    ScannedKnowledgeFile,
    build_manifest_hash,
    build_plan_id,
    build_sync_changes,
    scan_knowledge_directory,
)


def test_scans_one_thousand_markdown_files_deterministically(
    knowledge_store: tuple[KnowledgeStore, Path],
) -> None:
    store, vault = knowledge_store
    nested = vault / "notes"
    nested.mkdir()
    for index in range(1000):
        (nested / f"{index:04d}.md").write_text(f"# Note {index}\n", encoding="utf-8")
    (nested / "ignored.txt").write_text("ignored", encoding="utf-8")

    scanned = scan_knowledge_directory(
        store,
        "vault",
        "notes",
        workspace_id="workspace-1",
        pipeline_version="pipeline-v1",
    )

    assert len(scanned) == 1000
    assert scanned[0].relative_path == "notes/0000.md"
    assert scanned[-1].relative_path == "notes/0999.md"
    assert len({item.idempotency_key for item in scanned}) == 1000


def test_rejects_traversal_and_skips_symlinks(
    knowledge_store: tuple[KnowledgeStore, Path], tmp_path: Path
) -> None:
    store, vault = knowledge_store
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("# Outside\n", encoding="utf-8")
    (vault / "linked").symlink_to(outside, target_is_directory=True)

    scanned = scan_knowledge_directory(
        store,
        "vault",
        ".",
        workspace_id="workspace-1",
        pipeline_version="pipeline-v1",
    )

    assert scanned == []
    with pytest.raises(KnowledgeScanError, match="invalid relative source directory"):
        scan_knowledge_directory(
            store,
            "vault",
            "../outside",
            workspace_id="workspace-1",
            pipeline_version="pipeline-v1",
        )


def test_rejects_a_requested_directory_reached_through_an_internal_symlink(
    knowledge_store: tuple[KnowledgeStore, Path],
) -> None:
    store, vault = knowledge_store
    target = vault / "real"
    target.mkdir()
    (target / "note.md").write_text("# Internal\n", encoding="utf-8")
    (vault / "alias").symlink_to(target, target_is_directory=True)

    with pytest.raises(KnowledgeScanError, match="invalid relative source directory"):
        scan_knowledge_directory(
            store,
            "vault",
            "alias",
            workspace_id="workspace-1",
            pipeline_version="pipeline-v1",
        )


def test_scans_supported_markdown_html_and_pdf_files(
    knowledge_store: tuple[KnowledgeStore, Path],
) -> None:
    store, vault = knowledge_store
    (vault / "note.md").write_text("# Note\n", encoding="utf-8")
    (vault / "page.html").write_text("<h1>Page</h1>", encoding="utf-8")
    (vault / "manual.pdf").write_bytes(b"%PDF-1.4\n")
    (vault / "ignored.txt").write_text("ignored", encoding="utf-8")

    scanned = scan_knowledge_directory(
        store,
        "vault",
        ".",
        workspace_id="workspace-1",
        pipeline_version="pipeline-v2",
    )

    assert [item.relative_path for item in scanned] == [
        "manual.pdf",
        "note.md",
        "page.html",
    ]


def test_manifest_diff_is_deterministic_and_bounds_deletions_to_scan_scope() -> None:
    scanned = [
        ScannedKnowledgeFile("notes/added.md", "sha256:added", "scan-added"),
        ScannedKnowledgeFile("notes/changed.md", "sha256:new", "scan-changed"),
    ]
    manifest = {
        "notes/changed.md": ("sha256:old", "present"),
        "notes/deleted.md": ("sha256:deleted", "present"),
        "outside/keep.md": ("sha256:outside", "present"),
        "notes/already-deleted.md": ("sha256:tombstone", "deleted"),
    }

    first = build_sync_changes(
        scanned,
        manifest,
        workspace_id="workspace-1",
        source_root_id="vault",
        relative_directory="notes",
        pipeline_version="pipeline-v1",
    )
    repeated = build_sync_changes(
        list(reversed(scanned)),
        manifest,
        workspace_id="workspace-1",
        source_root_id="vault",
        relative_directory="notes",
        pipeline_version="pipeline-v1",
    )

    assert first == repeated
    assert [(item.relative_path, item.change_kind) for item in first] == [
        ("notes/added.md", "added"),
        ("notes/changed.md", "modified"),
        ("notes/deleted.md", "deleted"),
    ]
    assert all(item.relative_path != "outside/keep.md" for item in first)
    first_id = build_plan_id(
        workspace_id="workspace-1",
        source_root_id="vault",
        relative_directory="notes",
        pipeline_version="pipeline-v1",
        base_watermark=3,
        changes=first,
    )
    repeated_id = build_plan_id(
        workspace_id="workspace-1",
        source_root_id="vault",
        relative_directory="notes",
        pipeline_version="pipeline-v1",
        base_watermark=3,
        changes=repeated,
    )
    assert first_id == repeated_id
    assert len(first_id[0]) <= 36

    manifest_hash = build_manifest_hash(manifest, first)
    repeated_hash = build_manifest_hash(manifest, repeated)
    assert manifest_hash == repeated_hash
    assert manifest_hash != build_manifest_hash(manifest, ())
