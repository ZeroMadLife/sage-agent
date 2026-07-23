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


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        ".git/config",
        ".ssh/id_ed25519",
        ".codex/config.toml",
        ".claude/settings.json",
        ".sage/usage.sqlite3",
        "nested/credentials.json",
        ".npmrc",
    ],
)
def test_workspace_rejects_protected_credential_paths(
    tmp_path: Path,
    path: str,
) -> None:
    workspace = WorkspaceContext(root=tmp_path)

    with pytest.raises(ValueError, match="protected by workspace policy"):
        workspace.path(path)


@pytest.mark.parametrize(
    "path",
    [".env.example", ".env.production.sample", ".env.template"],
)
def test_workspace_allows_non_secret_environment_templates(
    tmp_path: Path,
    path: str,
) -> None:
    workspace = WorkspaceContext(root=tmp_path)

    assert workspace.path(path) == (tmp_path / path).resolve()


def test_workspace_rejects_symlink_to_protected_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=value\n", encoding="utf-8")
    (tmp_path / "visible.txt").symlink_to(tmp_path / ".env")
    workspace = WorkspaceContext(root=tmp_path)

    with pytest.raises(ValueError, match="protected by workspace policy"):
        workspace.path("visible.txt")


def test_workspace_internal_path_allows_sage_control_artifacts_but_not_escape(
    tmp_path: Path,
) -> None:
    workspace = WorkspaceContext(root=tmp_path)

    assert (
        workspace.internal_path(".coding/plans/test.md")
        == (tmp_path / ".coding/plans/test.md").resolve()
    )
    with pytest.raises(ValueError, match="escapes workspace root"):
        workspace.internal_path("../outside.txt")


def test_clip_truncates_long_tool_output() -> None:
    """Long tool output is clipped with a visible truncation marker."""
    clipped = clip("abcdef", limit=4)

    assert clipped.startswith("abcd")
    assert "truncated" in clipped
