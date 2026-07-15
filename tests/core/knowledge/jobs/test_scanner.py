"""Safe directory scanner coverage."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.knowledge import KnowledgeStore
from core.knowledge.jobs import KnowledgeScanError, scan_knowledge_directory


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
