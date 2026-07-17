"""WorkspaceDiffTracker tests: bounded diff artifact for coding runs."""

import subprocess
from pathlib import Path

from core.coding.context.workspace_diff import (
    MAX_DIFF_FILES,
    WorkspaceDiffTracker,
)


def test_clean_run_empty_diff(tmp_path: Path) -> None:
    """No file changes between snapshots -> empty diff."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run()
    diff = tracker.snapshot_after_run("run_clean")

    assert diff.run_id == "run_clean"
    assert diff.changed_files == []
    assert diff.file_count == 0
    assert diff.truncated is False


def test_write_file_produces_diff(tmp_path: Path) -> None:
    """Writing a new file after the before snapshot shows up as 'added'."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run()

    (tmp_path / "note.txt").write_text("hello world\n", encoding="utf-8")
    diff = tracker.snapshot_after_run("run_add")

    assert diff.file_count == 1
    change = diff.changed_files[0]
    assert change.path == "note.txt"
    assert change.status == "added"
    assert change.binary is False
    assert change.after_hash
    assert change.before_hash == ""
    # The added diff should reference the new file.
    assert "note.txt" in change.diff
    assert "+hello world" in change.diff


def test_modify_file_produces_unified_diff(tmp_path: Path) -> None:
    """Modifying a text file produces a real unified diff patch."""
    (tmp_path / "src.py").write_text("line one\nline two\n", encoding="utf-8")
    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run()

    (tmp_path / "src.py").write_text("line one\nline two edited\nline three\n", encoding="utf-8")
    diff = tracker.snapshot_after_run("run_modify")

    assert diff.file_count == 1
    change = diff.changed_files[0]
    assert change.path == "src.py"
    assert change.status == "modified"
    assert change.binary is False
    assert change.before_hash
    assert change.after_hash
    assert change.before_hash != change.after_hash
    # Unified diff markers are present.
    assert "--- a/src.py" in change.diff
    assert "+++ b/src.py" in change.diff
    assert "-line two" in change.diff
    assert "+line two edited" in change.diff
    assert "+line three" in change.diff


def test_delete_file_in_diff(tmp_path: Path) -> None:
    """Deleting a file after the before snapshot shows up as 'deleted'."""
    (tmp_path / "keep.txt").write_text("keep\n", encoding="utf-8")
    (tmp_path / "drop.txt").write_text("drop\n", encoding="utf-8")
    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run()

    (tmp_path / "drop.txt").unlink()
    diff = tracker.snapshot_after_run("run_delete")

    statuses = {change.path: change.status for change in diff.changed_files}
    assert statuses["drop.txt"] == "deleted"
    deleted = next(c for c in diff.changed_files if c.path == "drop.txt")
    assert deleted.before_hash
    assert deleted.after_hash == ""


def test_ignored_secrets_not_in_diff(tmp_path: Path) -> None:
    """.env files and .git/.coding dirs never appear in the diff."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=token\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (tmp_path / ".coding").mkdir()
    (tmp_path / ".coding" / "state.json").write_text("{}", encoding="utf-8")
    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run()

    # Mutate the .env (a secret) and a .git file after the snapshot; neither
    # should ever be tracked, so they must not appear in the diff.
    (tmp_path / ".env").write_text("SECRET=leaked\n", encoding="utf-8")
    (tmp_path / ".git" / "config").write_text("[new]\n", encoding="utf-8")
    diff = tracker.snapshot_after_run("run_secrets")

    paths = {change.path for change in diff.changed_files}
    assert ".env" not in paths
    assert ".git/config" not in paths
    assert ".coding/state.json" not in paths


def test_sage_usage_database_not_in_diff_but_sage_config_is(tmp_path: Path) -> None:
    """Runtime usage writes must not be reported as a source-code change."""
    sage_dir = tmp_path / ".sage"
    sage_dir.mkdir()
    (sage_dir / "usage.sqlite3").write_bytes(b"before")
    (sage_dir / "config.toml").write_text("[model]\nname = 'sage'\n", encoding="utf-8")
    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run()

    (sage_dir / "usage.sqlite3").write_bytes(b"after")
    (sage_dir / "usage.sqlite3-wal").write_bytes(b"journal")
    (sage_dir / "config.toml").write_text("[model]\nname = 'updated'\n", encoding="utf-8")

    diff = tracker.snapshot_after_run("run_usage")

    assert [change.path for change in diff.changed_files] == [".sage/config.toml"]


def test_binary_file_marked_binary(tmp_path: Path) -> None:
    """A file with null bytes is marked binary and has no diff content."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run()

    # Write a file whose leading bytes contain a null byte -> detected as binary.
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02\x03binary")
    diff = tracker.snapshot_after_run("run_binary")

    change = next(c for c in diff.changed_files if c.path == "blob.bin")
    assert change.status == "added"
    assert change.binary is True
    # Binary files must not carry a unified diff body.
    assert change.diff == ""


def test_large_diff_truncated(tmp_path: Path) -> None:
    """More than MAX_DIFF_FILES changed files -> truncated=True and capped list."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run()

    # Create more changed files than the cap.
    for i in range(MAX_DIFF_FILES + 5):
        (tmp_path / f"f{i:03d}.txt").write_text(f"content {i}\n", encoding="utf-8")
    diff = tracker.snapshot_after_run("run_truncated")

    assert diff.truncated is True
    assert len(diff.changed_files) == MAX_DIFF_FILES
    assert diff.file_count == MAX_DIFF_FILES


def test_symlink_not_followed(tmp_path: Path) -> None:
    """Symlinks are not diffed."""
    (tmp_path / "real.txt").write_text("secret", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")
    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run()
    (tmp_path / "real.txt").write_text("changed", encoding="utf-8")
    diff = tracker.snapshot_after_run("run_1")

    paths = [f.path for f in diff.changed_files]
    assert "link.txt" not in paths


def test_large_file_hash_detection(tmp_path: Path) -> None:
    """Files >256KB still have hash for change detection."""
    big = "x" * (300 * 1024)
    (tmp_path / "big.txt").write_text(big, encoding="utf-8")
    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run()
    (tmp_path / "big.txt").write_text("y" * (300 * 1024), encoding="utf-8")
    diff = tracker.snapshot_after_run("run_1")

    changes = [f for f in diff.changed_files if f.path == "big.txt"]
    assert len(changes) == 1
    assert changes[0].status == "modified"


def test_truncated_uses_changed_count_not_total(tmp_path: Path) -> None:
    """truncated is based on changed files, not total workspace files."""
    for i in range(60):
        (tmp_path / f"f{i}.txt").write_text(f"content {i}", encoding="utf-8")
    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run()
    (tmp_path / "f0.txt").write_text("changed", encoding="utf-8")
    (tmp_path / "f1.txt").write_text("changed", encoding="utf-8")
    diff = tracker.snapshot_after_run("run_1")

    assert diff.truncated is False
    assert diff.file_count == 2


def test_git_workspace_uses_index_and_excludes_ignored_untracked_tree(
    tmp_path: Path,
) -> None:
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=tmp_path,
        check=True,
    )
    (tmp_path / ".gitignore").write_text("ignored-cache/\n", encoding="utf-8")
    tracked = tmp_path / "tracked.py"
    tracked.write_text("value = 1\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", ".gitignore", "tracked.py"],
        cwd=tmp_path,
        check=True,
    )
    ignored = tmp_path / "ignored-cache"
    ignored.mkdir()
    for index in range(100):
        (ignored / f"entry-{index}.txt").write_text("ignored", encoding="utf-8")

    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run("run_git")
    tracked.write_text("value = 2\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("visible\n", encoding="utf-8")
    (ignored / "late.txt").write_text("still ignored", encoding="utf-8")

    diff = tracker.snapshot_after_run("run_git")

    assert {change.path for change in diff.changed_files} == {"new.txt", "tracked.py"}
    assert diff.truncated is False


def test_non_git_workspace_scan_is_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "core.coding.context.workspace_diff.MAX_SNAPSHOT_FILES",
        2,
    )
    for index in range(5):
        (tmp_path / f"file-{index}.txt").write_text(str(index), encoding="utf-8")
    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run("run_bounded")
    (tmp_path / "file-0.txt").write_text("changed", encoding="utf-8")

    diff = tracker.snapshot_after_run("run_bounded")

    assert diff.truncated is True
    assert [change.path for change in diff.changed_files] == ["file-0.txt"]


def test_workspace_diff_baseline_is_bound_to_run(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    tracker = WorkspaceDiffTracker(tmp_path)
    tracker.snapshot_before_run("run_a")

    try:
        tracker.snapshot_after_run("run_b")
    except RuntimeError as exc:
        assert str(exc) == "workspace diff baseline belongs to another run"
    else:
        raise AssertionError("cross-run baseline must be rejected")
