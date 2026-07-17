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


def test_git_controller_reports_dirty_paths_without_touching_them(tmp_path) -> None:
    root = _repository(tmp_path)
    (root / "tracked.txt").write_text("human edit\n", encoding="utf-8")
    (root / "frontend/src/views").mkdir(parents=True)
    untracked = root / "frontend/src/views/HumanView.vue"
    untracked.write_text("<template />\n", encoding="utf-8")
    git = GitController(root, remote="origin", target_branch="dev/sage-v7")

    status = git.require_integration_root(allow_dirty=True)

    assert status.dirty is True
    assert status.dirty_paths == (
        "frontend/src/views/HumanView.vue",
        "tracked.txt",
    )
    assert (root / "tracked.txt").read_text(encoding="utf-8") == "human edit\n"
    assert untracked.exists()


def test_git_controller_includes_unpushed_commit_paths_in_human_changes(tmp_path) -> None:
    root = _repository(tmp_path)
    (root / "local-only.txt").write_text("local commit\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "local only")
    (root / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    git = GitController(root, remote="origin", target_branch="dev/sage-v7")
    remote_sha = git.remote_sha()

    paths = git.human_change_paths(git.root_status(), remote_sha)

    assert paths == ("local-only.txt", "untracked.txt")


def test_git_controller_captures_visual_diff_and_discards_managed_worktree(tmp_path) -> None:
    root = _repository(tmp_path)
    view = root / "frontend/src/views/ExampleView.vue"
    view.parent.mkdir(parents=True)
    view.write_text(
        "<template><main class=\"old\">Text</main></template>\n"
        "<script setup lang=\"ts\">const count = 1</script>\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "add view")
    _git(root, "push", "origin", "dev/sage-v7")
    git = GitController(root, remote="origin", target_branch="dev/sage-v7")
    base_sha = git.remote_sha()
    worktree = tmp_path / "worktrees/run-visual"
    git.create_detached_worktree(worktree, base_sha)
    candidate = worktree / "frontend/src/views/ExampleView.vue"
    candidate.write_text(
        "<template><main class=\"fixed\">Text</main></template>\n"
        "<script setup lang=\"ts\">const count = 1</script>\n",
        encoding="utf-8",
    )

    snapshot = git.diff_snapshot(worktree, base_sha=base_sha)

    assert snapshot.changed_files == ("frontend/src/views/ExampleView.vue",)
    assert snapshot.changed_lines == 2
    assert snapshot.behavior_changed is False
    git.remove_managed_worktree(worktree, discard_changes=True)
    assert not worktree.exists()


def test_git_controller_marks_vue_script_changes_as_behavior(tmp_path) -> None:
    root = _repository(tmp_path)
    view = root / "frontend/src/views/ExampleView.vue"
    view.parent.mkdir(parents=True)
    view.write_text(
        "<template><main>Text</main></template>\n"
        "<script setup lang=\"ts\">const count = 1</script>\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "add view")
    _git(root, "push", "origin", "dev/sage-v7")
    git = GitController(root, remote="origin", target_branch="dev/sage-v7")
    base_sha = git.remote_sha()
    worktree = tmp_path / "worktrees/run-behavior"
    git.create_detached_worktree(worktree, base_sha)
    candidate = worktree / "frontend/src/views/ExampleView.vue"
    candidate.write_text(
        "<template><main>Text</main></template>\n"
        "<script setup lang=\"ts\">const count = 2</script>\n",
        encoding="utf-8",
    )

    snapshot = git.diff_snapshot(worktree, base_sha=base_sha)

    assert snapshot.behavior_changed is True
    git.remove_managed_worktree(worktree, discard_changes=True)


def test_git_controller_marks_vue_directive_changes_as_behavior(tmp_path) -> None:
    root = _repository(tmp_path)
    view = root / "frontend/src/views/ExampleView.vue"
    view.parent.mkdir(parents=True)
    view.write_text(
        "<template><main v-if=\"ready\" class=\"old\">Text</main></template>\n"
        "<script setup lang=\"ts\">const ready = true</script>\n",
        encoding="utf-8",
    )
    _git(root, "add", ".")
    _git(root, "commit", "-m", "add view")
    _git(root, "push", "origin", "dev/sage-v7")
    git = GitController(root, remote="origin", target_branch="dev/sage-v7")
    base_sha = git.remote_sha()
    worktree = tmp_path / "worktrees/run-directive"
    git.create_detached_worktree(worktree, base_sha)
    candidate = worktree / "frontend/src/views/ExampleView.vue"
    candidate.write_text(
        "<template><main v-if=\"visible\" class=\"old\">Text</main></template>\n"
        "<script setup lang=\"ts\">const ready = true</script>\n",
        encoding="utf-8",
    )

    snapshot = git.diff_snapshot(worktree, base_sha=base_sha)

    assert snapshot.behavior_changed is True
    git.remove_managed_worktree(worktree, discard_changes=True)


def test_git_controller_commits_exact_candidate_and_pushes_without_force(tmp_path) -> None:
    root = _repository(tmp_path)
    view = root / "frontend/src/views/ExampleView.vue"
    view.parent.mkdir(parents=True)
    view.write_text("<template><main class=\"old\">文本</main></template>\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "add view")
    _git(root, "push", "origin", "dev/sage-v7")
    git = GitController(root, remote="origin", target_branch="dev/sage-v7")
    base_sha = git.remote_sha()
    worktree = tmp_path / "worktrees/run-pr"
    git.create_detached_worktree(worktree, base_sha)
    relative = "frontend/src/views/ExampleView.vue"
    (worktree / relative).write_text(
        "<template><main class=\"fixed\">文本</main></template>\n", encoding="utf-8"
    )
    branch = "codex/loop-frontend-abcdef123456"

    head_sha = git.commit_candidate(
        worktree,
        base_sha=base_sha,
        branch=branch,
        allowed_paths=(relative,),
        message="fix(loop): 修复示例间距",
    )
    git.push_candidate(worktree, branch=branch, head_sha=head_sha)

    remote_head = _git(root, "ls-remote", "origin", f"refs/heads/{branch}").stdout.split()[0]
    assert remote_head == head_sha
    assert _git(worktree, "status", "--porcelain").stdout == ""
    git.remove_managed_worktree(worktree, discard_changes=True)
