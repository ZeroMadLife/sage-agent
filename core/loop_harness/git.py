"""Fixed Git operations used by the deterministic controller."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.loop_harness.errors import LoopBlockedError


@dataclass(frozen=True, slots=True)
class RootStatus:
    branch: str
    head_sha: str
    dirty: bool


class GitController:
    def __init__(self, repo_root: Path, *, remote: str, target_branch: str) -> None:
        self.repo_root = repo_root
        self.remote = remote
        self.target_branch = target_branch

    def root_status(self) -> RootStatus:
        branch = self._run("symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
        head_sha = self._run("rev-parse", "HEAD").stdout.strip()
        dirty = bool(self._run("status", "--porcelain=v1", "--untracked-files=all").stdout.strip())
        return RootStatus(branch=branch, head_sha=head_sha, dirty=dirty)

    def require_clean_integration_root(self) -> RootStatus:
        try:
            status = self.root_status()
        except RuntimeError as exc:
            raise LoopBlockedError("BLOCKED_GIT", str(exc)) from exc
        if status.branch != self.target_branch:
            raise LoopBlockedError(
                "BLOCKED_ROOT_BRANCH",
                f"integration root is on {status.branch}, expected {self.target_branch}",
            )
        if status.dirty:
            raise LoopBlockedError(
                "BLOCKED_ROOT_DIRTY", "integration root has uncommitted or untracked files"
            )
        return status

    def fetch(self) -> None:
        self._run("fetch", "--prune", self.remote, timeout=120)

    def remote_sha(self) -> str:
        sha = self._run(
            "rev-parse", f"refs/remotes/{self.remote}/{self.target_branch}"
        ).stdout.strip()
        if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
            raise LoopBlockedError("BLOCKED_BASE_SHA", "remote base SHA is invalid")
        return sha

    def require_root_at_sha(self, status: RootStatus, remote_sha: str) -> None:
        if status.head_sha == remote_sha:
            return
        local_is_ancestor = self._is_ancestor(status.head_sha, remote_sha)
        remote_is_ancestor = self._is_ancestor(remote_sha, status.head_sha)
        if local_is_ancestor:
            code = "BLOCKED_ROOT_BEHIND"
        elif remote_is_ancestor:
            code = "BLOCKED_ROOT_AHEAD"
        else:
            code = "BLOCKED_ROOT_DIVERGED"
        raise LoopBlockedError(code, "integration root does not exactly match remote target")

    def create_detached_worktree(self, destination: Path, base_sha: str) -> None:
        if destination.exists():
            raise LoopBlockedError("BLOCKED_WORKTREE_EXISTS", "run worktree already exists")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._run("worktree", "add", "--detach", str(destination), base_sha, timeout=120)

    def remove_clean_worktree(self, destination: Path) -> None:
        if not destination.exists():
            return
        result = self._run_at(
            destination,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        if result.stdout.strip():
            raise LoopBlockedError(
                "BLOCKED_WORKTREE_DIRTY", "worker worktree contains unexpected changes"
            )
        self._run("worktree", "remove", str(destination), timeout=120)
        if destination.exists():
            raise LoopBlockedError(
                "BLOCKED_WORKTREE_CLEANUP", "Git did not remove the run worktree"
            )

    def prune_missing_worktrees(self) -> None:
        self._run("worktree", "prune", "--expire", "now")

    def _is_ancestor(self, older: str, newer: str) -> bool:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", older, newer],
            cwd=self.repo_root,
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
        if result.returncode not in {0, 1}:
            raise LoopBlockedError("BLOCKED_GIT", result.stderr.strip() or "Git failed")
        return result.returncode == 0

    def _run(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        return self._run_at(self.repo_root, *args, timeout=timeout)

    @staticmethod
    def _run_at(cwd: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        if shutil.which("git") is None:
            raise RuntimeError("git executable is unavailable")
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Git command failed")
        return result
