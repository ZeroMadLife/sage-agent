"""Loop Harness configuration and controlled local paths."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


class LoopConfigError(RuntimeError):
    """The local harness configuration is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class LoopConfig:
    repo_root: Path
    controller_root: Path
    state_root: Path
    worktree_root: Path
    codex_bin: Path
    gh_bin: Path = Path("/opt/homebrew/bin/gh")
    cc_connect_bin: Path = Path("/opt/homebrew/bin/cc-connect")
    github_repository: str = "ZeroMadLife/sage-agent"
    target_branch: str = "dev/sage-v7"
    remote: str = "origin"
    lease_seconds: int = 90 * 60
    run_timeout_seconds: int = 55 * 60
    scanner_timeout_seconds: int = 10 * 60
    fixer_timeout_seconds: int = 15 * 60
    minimum_free_bytes: int = 2 * 1024 * 1024 * 1024

    @property
    def database_path(self) -> Path:
        return self.state_root / "state.sqlite3"

    @property
    def manifest_path(self) -> Path:
        return self.state_root / "manifest.json"

    @property
    def log_path(self) -> Path:
        return self.state_root / "logs" / "loop.log"

    @property
    def reports_root(self) -> Path:
        return self.state_root / "reports"

    @classmethod
    def from_env(cls) -> LoopConfig:
        controller_root = Path(__file__).resolve().parents[2]
        repo_root = Path(
            os.environ.get("SAGE_LOOP_REPO_ROOT", "/Users/zeromadlife/Desktop/tour-agent")
        ).expanduser()
        state_root = Path(
            os.environ.get("SAGE_LOOP_STATE_ROOT", "~/.local/state/sage-loop")
        ).expanduser()
        worktree_root = Path(
            os.environ.get("SAGE_LOOP_WORKTREE_ROOT", "~/.local/share/sage-loop/worktrees")
        ).expanduser()
        codex_bin = _resolve_codex(os.environ.get("SAGE_LOOP_CODEX_BIN"))
        gh_bin = _resolve_executable(os.environ.get("SAGE_LOOP_GH_BIN"), "gh")
        cc_connect_bin = _resolve_executable(
            os.environ.get("SAGE_LOOP_CC_CONNECT_BIN"), "cc-connect"
        )
        return cls(
            repo_root=repo_root.resolve(),
            controller_root=controller_root,
            state_root=state_root.absolute(),
            worktree_root=worktree_root.absolute(),
            codex_bin=codex_bin,
            gh_bin=gh_bin,
            cc_connect_bin=cc_connect_bin,
        )

    def ensure_local_directories(self) -> None:
        for path in (
            self.state_root,
            self.state_root / "logs",
            self.reports_root,
            self.state_root / "locks",
            self.worktree_root,
        ):
            _ensure_private_directory(path)

    def validate_static(self) -> None:
        if not self.repo_root.is_dir():
            raise LoopConfigError(f"repository root does not exist: {self.repo_root}")
        if not (self.repo_root / ".git").exists():
            raise LoopConfigError(f"repository root is not a Git checkout: {self.repo_root}")
        if not self.controller_root.is_dir():
            raise LoopConfigError("controller root does not exist")
        if not self.codex_bin.is_file() or not os.access(self.codex_bin, os.X_OK):
            raise LoopConfigError(f"Codex executable is unavailable: {self.codex_bin}")
        if self.target_branch not in {"dev/sage-v7"}:
            raise LoopConfigError("unsupported target branch")
        if self.remote != "origin":
            raise LoopConfigError("unsupported Git remote")
        if self.github_repository != "ZeroMadLife/sage-agent":
            raise LoopConfigError("unsupported GitHub repository")
        if self.scanner_timeout_seconds < 1 or self.fixer_timeout_seconds < 1:
            raise LoopConfigError("Worker timeouts must be positive")
        if self.scanner_timeout_seconds + self.fixer_timeout_seconds > self.run_timeout_seconds:
            raise LoopConfigError("Worker timeouts exceed the run budget")
        if _contains(self.repo_root, self.state_root) or _contains(
            self.controller_root, self.state_root
        ):
            raise LoopConfigError("state directory must be outside Git checkouts")
        if _contains(self.repo_root, self.worktree_root) or _contains(
            self.controller_root, self.worktree_root
        ):
            raise LoopConfigError("Loop worktrees must be outside existing Git checkouts")
        if _contains(self.state_root, self.worktree_root) or _contains(
            self.worktree_root, self.state_root
        ):
            raise LoopConfigError("state and worktree directories must be separate")


def _resolve_codex(explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.extend(
        (
            Path("/opt/homebrew/bin/codex"),
            Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        )
    )
    discovered = shutil.which("codex")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file() and os.access(resolved, os.X_OK):
            return resolved
    return Path(explicit or "codex").expanduser()


def _resolve_executable(explicit: str | None, name: str) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    discovered = shutil.which(name)
    return Path(discovered).resolve() if discovered else Path(name)


def _ensure_private_directory(path: Path) -> None:
    for component in (path, *path.parents):
        if component.exists() and component.is_symlink():
            raise LoopConfigError(f"managed directory path must not contain a symlink: {component}")
        if component == component.parent:
            break
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _contains(parent: Path, child: Path) -> bool:
    return child == parent or child.is_relative_to(parent)
