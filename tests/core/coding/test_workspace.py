"""Coding workspace safety tests."""

from pathlib import Path

import pytest

from core.coding.context import WorkspaceContext, clip


def test_workspace_resolves_relative_paths_inside_root(tmp_path: Path) -> None:
    """Relative paths resolve to absolute paths under the configured root."""
    workspace = WorkspaceContext(root=tmp_path)

    resolved = workspace.path("src/app.py")

    assert resolved == (tmp_path / "src" / "app.py").resolve()


def test_workspace_rejects_parent_directory_escape(tmp_path: Path) -> None:
    """Workspace paths cannot escape the root with ../../ traversal."""
    workspace = WorkspaceContext(root=tmp_path)

    with pytest.raises(ValueError, match="escapes workspace root"):
        workspace.path("../outside.txt")


def test_workspace_rejects_absolute_path_outside_root(tmp_path: Path) -> None:
    """Absolute paths outside the workspace root are rejected."""
    workspace = WorkspaceContext(root=tmp_path)

    with pytest.raises(ValueError, match="escapes workspace root"):
        workspace.path("/tmp/outside.txt")


def test_clip_truncates_long_tool_output() -> None:
    """Long tool output is clipped with a visible truncation marker."""
    clipped = clip("abcdef", limit=4)

    assert clipped.startswith("abcd")
    assert "truncated" in clipped
