from __future__ import annotations

from pathlib import Path

from core.knowledge import KnowledgeSourceRoot
from core.knowledge.sources import default_source_adapter_registry, fetch_source_by_key


async def test_fetch_source_by_key_discovers_revision_server_side(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "note.md").write_text("# Note\n", encoding="utf-8")
    source = KnowledgeSourceRoot("vault", "obsidian", "Vault", tmp_path)

    artifact = await fetch_source_by_key(
        default_source_adapter_registry(),
        source,
        "nested/note.md",
    )

    assert artifact.source_key == "nested/note.md"
    assert artifact.source_revision.startswith("sha256:")
    assert artifact.content == b"# Note\n"
