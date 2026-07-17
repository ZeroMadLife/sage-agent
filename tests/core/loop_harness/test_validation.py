from __future__ import annotations

import subprocess

import pytest

from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.validation import FrontendValidator


def test_frontend_validator_runs_fixed_commands_with_temporary_dependencies(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    dependencies = repo_root / "frontend/node_modules"
    dependencies.mkdir(parents=True)
    worktree = tmp_path / "worktree"
    frontend = worktree / "frontend"
    frontend.mkdir(parents=True)
    calls: list[tuple[tuple[str, ...], object]] = []

    def fake_run(command, *, cwd, timeout, env):
        assert (frontend / "node_modules").is_symlink()
        calls.append((tuple(command), cwd))
        return subprocess.CompletedProcess(command, 0, "", "")

    result = FrontendValidator(repo_root=repo_root, run_command=fake_run).validate(worktree)

    assert result.passed is True
    assert [step.name for step in result.steps] == [
        "git-diff-check",
        "frontend-test",
        "frontend-build",
    ]
    assert calls[0][0] == ("git", "diff", "--check")
    assert calls[1][0] == ("npm", "run", "test", "--", "--run")
    assert not (frontend / "node_modules").exists()


def test_frontend_validator_cleans_dependency_link_after_failure(tmp_path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "frontend/node_modules").mkdir(parents=True)
    worktree = tmp_path / "worktree"
    frontend = worktree / "frontend"
    frontend.mkdir(parents=True)

    def fake_run(command, *, cwd, timeout, env):
        return subprocess.CompletedProcess(command, 1, "", "failed")

    validator = FrontendValidator(repo_root=repo_root, run_command=fake_run)

    with pytest.raises(LoopBlockedError) as exc:
        validator.validate(worktree)

    assert exc.value.code == "BLOCKED_VALIDATION"
    assert not (frontend / "node_modules").exists()
