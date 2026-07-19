"""Fixed Git operations used by the deterministic controller."""

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.models import DiffSnapshot

_LOOP_BRANCH_PATTERN = re.compile(r"codex/loop-frontend-[a-f0-9]{12}")


@dataclass(frozen=True, slots=True)
class RootStatus:
    branch: str
    head_sha: str
    dirty: bool
    dirty_paths: tuple[str, ...] = ()


class GitController:
    def __init__(self, repo_root: Path, *, remote: str, target_branch: str) -> None:
        self.repo_root = repo_root
        self.remote = remote
        self.target_branch = target_branch

    def root_status(self) -> RootStatus:
        branch = self._run("symbolic-ref", "--quiet", "--short", "HEAD").stdout.strip()
        head_sha = self._run("rev-parse", "HEAD").stdout.strip()
        payload = self._run(
            "status", "--porcelain=v1", "-z", "--untracked-files=all"
        ).stdout
        dirty_paths = _porcelain_paths(payload)
        return RootStatus(
            branch=branch,
            head_sha=head_sha,
            dirty=bool(dirty_paths),
            dirty_paths=dirty_paths,
        )

    def require_clean_integration_root(self) -> RootStatus:
        return self.require_integration_root(allow_dirty=False)

    def require_integration_root(self, *, allow_dirty: bool) -> RootStatus:
        try:
            status = self.root_status()
        except RuntimeError as exc:
            raise LoopBlockedError("BLOCKED_GIT", str(exc)) from exc
        if status.branch != self.target_branch:
            raise LoopBlockedError(
                "BLOCKED_ROOT_BRANCH",
                f"integration root is on {status.branch}, expected {self.target_branch}",
            )
        if status.dirty and not allow_dirty:
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

    def human_change_paths(self, status: RootStatus, remote_sha: str) -> tuple[str, ...]:
        paths = set(status.dirty_paths)
        if status.head_sha == remote_sha or self._is_ancestor(status.head_sha, remote_sha):
            return tuple(sorted(paths))
        if self._is_ancestor(remote_sha, status.head_sha):
            comparison = f"{remote_sha}..{status.head_sha}"
        else:
            comparison = f"{remote_sha}...{status.head_sha}"
        payload = self._run("diff", "--name-only", "-z", comparison, "--").stdout
        paths.update(
            PurePosixPath(path).as_posix() for path in payload.split("\0") if path
        )
        return tuple(sorted(paths))

    def create_detached_worktree(self, destination: Path, base_sha: str) -> None:
        if destination.exists():
            raise LoopBlockedError("BLOCKED_WORKTREE_EXISTS", "run worktree already exists")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._run("worktree", "add", "--detach", str(destination), base_sha, timeout=120)

    def remove_clean_worktree(self, destination: Path) -> None:
        self.remove_managed_worktree(destination, discard_changes=False)

    def diff_snapshot(self, worktree: Path, *, base_sha: str) -> DiffSnapshot:
        head_sha = self._run_at(worktree, "rev-parse", "HEAD").stdout.strip()
        if head_sha != base_sha:
            raise LoopBlockedError("BLOCKED_WORKER_COMMIT", "Worker changed worktree HEAD")
        status_payload = self._run_at(
            worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        ).stdout
        changed_files = _porcelain_paths(status_payload)
        numstat = self._run_at(worktree, "diff", "--numstat", "HEAD", "--").stdout
        additions = 0
        deletions = 0
        binary_files: list[str] = []
        tracked_paths: set[str] = set()
        for line in numstat.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                raise LoopBlockedError("BLOCKED_DIFF", "Git numstat output is malformed")
            added, deleted, path = parts
            tracked_paths.add(path)
            if added == "-" or deleted == "-":
                binary_files.append(path)
                continue
            additions += int(added)
            deletions += int(deleted)

        symlink_files: list[str] = []
        deleted_files: list[str] = []
        behavior_changed = False
        for relative in changed_files:
            file_path = worktree / relative
            if file_path.is_symlink():
                symlink_files.append(relative)
            if not file_path.exists() and not file_path.is_symlink():
                deleted_files.append(relative)
            if relative not in tracked_paths and file_path.is_file() and not file_path.is_symlink():
                payload = file_path.read_bytes()
                if b"\0" in payload:
                    binary_files.append(relative)
                else:
                    additions += len(payload.splitlines())
            if relative.endswith(".vue") and relative.startswith(
                ("frontend/src/components/", "frontend/src/views/")
            ):
                before = self._blob_at(base_sha, relative)
                after = (
                    file_path.read_bytes()
                    if file_path.is_file() and not file_path.is_symlink()
                    else b""
                )
                if _vue_behavior_signature(before) != _vue_behavior_signature(after):
                    behavior_changed = True

        return DiffSnapshot(
            changed_files=changed_files,
            additions=additions,
            deletions=deletions,
            binary_files=tuple(sorted(dict.fromkeys(binary_files))),
            symlink_files=tuple(sorted(symlink_files)),
            behavior_changed=behavior_changed,
            deleted_files=tuple(sorted(deleted_files)),
        )

    def diff_patch(self, worktree: Path, *, base_sha: str) -> str:
        head_sha = self._run_at(worktree, "rev-parse", "HEAD").stdout.strip()
        if head_sha != base_sha:
            raise LoopBlockedError("BLOCKED_WORKER_COMMIT", "Worker changed worktree HEAD")
        tracked = self._run_at(
            worktree, "diff", "--no-ext-diff", "--unified=3", "HEAD", "--"
        ).stdout
        untracked_payload = self._run_at(
            worktree, "ls-files", "--others", "--exclude-standard", "-z"
        ).stdout
        parts = [tracked]
        for relative in sorted(path for path in untracked_payload.split("\0") if path):
            file_path = worktree / relative
            try:
                content = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                raise LoopBlockedError(
                    "BLOCKED_DIFF", "untracked candidate file is not UTF-8 text"
                ) from exc
            generated = difflib.unified_diff(
                [],
                content.splitlines(),
                fromfile="/dev/null",
                tofile=f"b/{relative}",
                lineterm="",
            )
            parts.append("\n".join(generated) + "\n")
        return "".join(parts)

    def commit_candidate(
        self,
        worktree: Path,
        *,
        base_sha: str,
        branch: str,
        allowed_paths: tuple[str, ...],
        message: str,
    ) -> str:
        if _LOOP_BRANCH_PATTERN.fullmatch(branch) is None:
            raise LoopBlockedError("BLOCKED_PR_BRANCH", "Loop candidate branch is invalid")
        if not allowed_paths:
            raise LoopBlockedError("BLOCKED_DIFF", "candidate has no allowed paths")
        snapshot = self.diff_snapshot(worktree, base_sha=base_sha)
        if set(snapshot.changed_files) != set(allowed_paths):
            raise LoopBlockedError(
                "BLOCKED_DIFF_POLICY", "candidate paths changed before commit"
            )
        normalized_message = " ".join(message.split())
        if not normalized_message or len(normalized_message) > 120:
            raise LoopBlockedError("BLOCKED_COMMIT", "candidate commit message is invalid")

        self._run_at(worktree, "switch", "-c", branch)
        self._run_at(worktree, "add", "--", *allowed_paths)
        staged = self._run_at(
            worktree, "diff", "--cached", "--name-only", "-z", "--"
        ).stdout
        staged_paths = tuple(sorted(path for path in staged.split("\0") if path))
        if set(staged_paths) != set(allowed_paths):
            raise LoopBlockedError("BLOCKED_COMMIT", "staged paths do not match candidate")
        self._run_at(
            worktree,
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgSign=false",
            "-c",
            "user.name=Sage Loop Engineer",
            "-c",
            "user.email=sage-loop@localhost",
            "commit",
            "-m",
            normalized_message,
            timeout=120,
        )
        head_sha = self._run_at(worktree, "rev-parse", "HEAD").stdout.strip()
        if head_sha == base_sha or len(head_sha) != 40:
            raise LoopBlockedError("BLOCKED_COMMIT", "candidate commit SHA is invalid")
        if self._run_at(
            worktree, "status", "--porcelain=v1", "--untracked-files=all"
        ).stdout:
            raise LoopBlockedError("BLOCKED_COMMIT", "candidate worktree is dirty after commit")
        return head_sha

    def push_candidate(self, worktree: Path, *, branch: str, head_sha: str) -> None:
        if _LOOP_BRANCH_PATTERN.fullmatch(branch) is None:
            raise LoopBlockedError("BLOCKED_PR_BRANCH", "Loop candidate branch is invalid")
        actual_head = self._run_at(worktree, "rev-parse", "HEAD").stdout.strip()
        actual_branch = self._run_at(
            worktree, "symbolic-ref", "--quiet", "--short", "HEAD"
        ).stdout.strip()
        if actual_head != head_sha or actual_branch != branch:
            raise LoopBlockedError("BLOCKED_PR_HEAD_DRIFT", "candidate branch head changed")
        try:
            self._run_at(
                worktree,
                "push",
                "--porcelain",
                self.remote,
                f"HEAD:refs/heads/{branch}",
                timeout=120,
            )
        except RuntimeError as exc:
            raise LoopBlockedError("BLOCKED_GITHUB_PUSH", "candidate push failed") from exc

    def remove_local_candidate_branch(self, *, branch: str, head_sha: str) -> None:
        if _LOOP_BRANCH_PATTERN.fullmatch(branch) is None:
            raise LoopBlockedError("BLOCKED_PR_BRANCH", "Loop candidate branch is invalid")
        if len(head_sha) != 40 or any(
            character not in "0123456789abcdef" for character in head_sha
        ):
            raise LoopBlockedError("BLOCKED_PR_HEAD", "candidate head SHA is invalid")
        actual_head = self._run("rev-parse", f"refs/heads/{branch}").stdout.strip()
        if actual_head != head_sha:
            raise LoopBlockedError(
                "BLOCKED_PR_HEAD_DRIFT", "local candidate branch head changed"
            )
        root_branch = self._run(
            "symbolic-ref", "--quiet", "--short", "HEAD"
        ).stdout.strip()
        if root_branch == branch:
            raise LoopBlockedError(
                "BLOCKED_PR_BRANCH", "candidate branch is checked out in the integration root"
            )
        self._run("branch", "-D", branch)

    def remove_managed_worktree(self, destination: Path, *, discard_changes: bool) -> None:
        if not destination.exists():
            return
        resolved = destination.resolve()
        if resolved == self.repo_root.resolve():
            raise LoopBlockedError("BLOCKED_WORKTREE_CLEANUP", "refusing to remove repository root")
        top_level = Path(
            self._run_at(destination, "rev-parse", "--show-toplevel").stdout.strip()
        ).resolve()
        if top_level != resolved:
            raise LoopBlockedError(
                "BLOCKED_WORKTREE_CLEANUP", "cleanup target is not a managed worktree root"
            )
        result = self._run_at(
            destination,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        if result.stdout.strip() and not discard_changes:
            raise LoopBlockedError(
                "BLOCKED_WORKTREE_DIRTY", "worker worktree contains unexpected changes"
            )
        arguments = ["worktree", "remove"]
        if discard_changes:
            arguments.append("--force")
        arguments.append(str(destination))
        self._run(*arguments, timeout=120)
        if destination.exists():
            raise LoopBlockedError(
                "BLOCKED_WORKTREE_CLEANUP", "Git did not remove the run worktree"
            )

    def prune_missing_worktrees(self) -> None:
        self._run("worktree", "prune", "--expire", "now")

    def _blob_at(self, sha: str, path: str) -> bytes:
        result = subprocess.run(
            ["git", "show", f"{sha}:{path}"],
            cwd=self.repo_root,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if result.returncode == 0:
            return result.stdout
        if result.returncode == 128:
            return b""
        raise LoopBlockedError("BLOCKED_GIT", "could not read base file for diff policy")

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


def _porcelain_paths(payload: str) -> tuple[str, ...]:
    tokens = payload.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        if len(token) < 4 or token[2] != " ":
            raise RuntimeError("Git status output is malformed")
        status = token[:2]
        paths.append(PurePosixPath(token[3:]).as_posix())
        if any(marker in status for marker in ("R", "C")):
            if index >= len(tokens) or not tokens[index]:
                raise RuntimeError("Git rename status is malformed")
            paths.append(PurePosixPath(tokens[index]).as_posix())
            index += 1
    return tuple(sorted(dict.fromkeys(paths)))


_SCRIPT_PATTERN = re.compile(rb"<script\b[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_TEMPLATE_PATTERN = re.compile(
    rb"<template\b[^>]*>(.*?)</template\s*>", re.IGNORECASE | re.DOTALL
)
_TAG_PATTERN = re.compile(rb"<\s*(/?)\s*([A-Za-z][\w.-]*)([^>]*)>", re.DOTALL)
_ATTRIBUTE_PATTERN = re.compile(
    rb"([:@#]?[A-Za-z_][\w:.-]*)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?"
)
_INTERPOLATION_PATTERN = re.compile(rb"{{(.*?)}}", re.DOTALL)
_VISUAL_ATTRIBUTES = {b"class", b"style", b"title"}


def _vue_script(payload: bytes) -> tuple[bytes, ...]:
    return tuple(match.strip() for match in _SCRIPT_PATTERN.findall(payload))


def _vue_behavior_signature(payload: bytes) -> tuple[bytes, ...]:
    signature: list[bytes] = [b"script:" + block for block in _vue_script(payload)]
    for template in _TEMPLATE_PATTERN.findall(payload):
        signature.extend(b"expr:" + item.strip() for item in _INTERPOLATION_PATTERN.findall(template))
        for closing, tag, attributes in _TAG_PATTERN.findall(template):
            signature.append(b"tag:" + closing + tag.lower())
            if closing:
                continue
            for match in _ATTRIBUTE_PATTERN.finditer(attributes):
                name = match.group(1).lower()
                if name in _VISUAL_ATTRIBUTES or name.startswith((b"aria-", b"data-")):
                    continue
                value = next((group for group in match.groups()[1:] if group is not None), b"")
                signature.append(b"attr:" + name + b"=" + value.strip())
    return tuple(signature)
