from __future__ import annotations

import subprocess

import pytest

from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.git import GitController


def _git(root, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _repository(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)],
        check=True,
        capture_output=True,
        text=True,
    )
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.name", "Loop Test")
    _git(root, "config", "user.email", "loop@example.com")
    (root / "tracked.txt").write_text("one\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    _git(root, "branch", "-M", "dev/sage-v7")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-u", "origin", "dev/sage-v7")
    return root


def test_git_controller_creates_and_removes_clean_detached_worktree(tmp_path) -> None:
    root = _repository(tmp_path)
    git = GitController(root, remote="origin", target_branch="dev/sage-v7")
    status = git.require_clean_integration_root()
    git.fetch()
    remote_sha = git.remote_sha()
    git.require_root_at_sha(status, remote_sha)
    worktree = tmp_path / "worktrees/run-1"

    git.create_detached_worktree(worktree, remote_sha)

    assert (worktree / "tracked.txt").read_text(encoding="utf-8") == "one\n"
    assert not (worktree / "node_modules").exists()
    git.remove_clean_worktree(worktree)
    assert not worktree.exists()


def test_git_controller_never_removes_dirty_worktree(tmp_path) -> None:
    root = _repository(tmp_path)
    git = GitController(root, remote="origin", target_branch="dev/sage-v7")
    worktree = tmp_path / "worktrees/run-1"
    git.create_detached_worktree(worktree, git.remote_sha())
    (worktree / "unexpected.txt").write_text("keep me\n", encoding="utf-8")

    with pytest.raises(LoopBlockedError, match="unexpected changes"):
        git.remove_clean_worktree(worktree)

    assert (worktree / "unexpected.txt").exists()


def test_git_controller_blocks_dirty_integration_root(tmp_path) -> None:
    root = _repository(tmp_path)
    (root / "untracked.txt").write_text("human work\n", encoding="utf-8")
    git = GitController(root, remote="origin", target_branch="dev/sage-v7")

    with pytest.raises(LoopBlockedError) as exc:
        git.require_clean_integration_root()

    assert exc.value.code == "BLOCKED_ROOT_DIRTY"
