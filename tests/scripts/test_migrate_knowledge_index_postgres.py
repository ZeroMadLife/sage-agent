from __future__ import annotations

from pathlib import Path

from scripts.migrate_knowledge_index_postgres import _resolve_database_path


def test_resolve_database_path_uses_workspace_default(tmp_path: Path) -> None:
    workspace = tmp_path / "knowledge"

    assert _resolve_database_path(workspace, "") == workspace / ".sage" / "knowledge.sqlite3"


def test_resolve_database_path_preserves_explicit_path(tmp_path: Path) -> None:
    workspace = tmp_path / "knowledge"
    database = tmp_path / "state" / "knowledge.sqlite3"

    assert _resolve_database_path(workspace, database) == database
