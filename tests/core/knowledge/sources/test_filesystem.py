from __future__ import annotations

from pathlib import Path

import pytest

from core.knowledge import KnowledgeSourceRoot
from core.knowledge.sources import (
    FilesystemKnowledgeSourceAdapter,
    KnowledgeSourceCheckpointConflictError,
)


async def test_filesystem_adapter_pages_and_fetches_pinned_revisions(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "b.html").write_text("<h1>B</h1>", encoding="utf-8")
    source = KnowledgeSourceRoot("vault", "obsidian", "Vault", tmp_path)
    adapter = FilesystemKnowledgeSourceAdapter()

    first = await adapter.scan(source, ".", None, None, 1)
    second = await adapter.scan(source, ".", None, first.next_cursor, 1)

    assert [item.source_key for item in first.items + second.items] == ["a.md", "b.html"]
    assert first.target_checkpoint == second.target_checkpoint
    assert first.complete is False
    assert second.complete is True
    artifact = await adapter.fetch(source, first.items[0])
    assert artifact.content == b"# A\n"
    assert artifact.metadata == {"adapter_id": "filesystem", "adapter_version": "1"}


async def test_filesystem_adapter_rejects_cursor_after_source_changes(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# B\n", encoding="utf-8")
    source = KnowledgeSourceRoot("vault", "markdown", "Vault", tmp_path)
    adapter = FilesystemKnowledgeSourceAdapter()
    first = await adapter.scan(source, ".", None, None, 1)

    (tmp_path / "b.md").write_text("# Changed\n", encoding="utf-8")

    with pytest.raises(KnowledgeSourceCheckpointConflictError):
        await adapter.scan(source, ".", None, first.next_cursor, 1)
