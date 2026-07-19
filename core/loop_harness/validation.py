"""Fixed validation commands for Phase 2 frontend shadow diffs."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Protocol

from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.models import ValidationResult, ValidationStep


class CommandRunner(Protocol):
    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout: int,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]: ...


class FrontendValidator:
    """Run only Controller-owned commands; model-suggested commands are never executed."""

    def __init__(
        self,
        *,
        repo_root: Path,
        run_command: CommandRunner | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.run_command = run_command or _run_command

    def validate(self, worktree: Path) -> ValidationResult:
        frontend = worktree / "frontend"
        dependencies = self.repo_root / "frontend/node_modules"
        dependency_link = frontend / "node_modules"
        if not frontend.is_dir():
            raise LoopBlockedError("BLOCKED_VALIDATION", "frontend directory is missing")
        if not dependencies.is_dir() or dependencies.is_symlink():
            raise LoopBlockedError(
                "BLOCKED_VALIDATION_DEPS", "trusted frontend dependencies are unavailable"
            )
        if dependency_link.exists() or dependency_link.is_symlink():
            raise LoopBlockedError(
                "BLOCKED_VALIDATION_DEPS", "worktree dependency path already exists"
            )

        dependency_link.symlink_to(dependencies, target_is_directory=True)
        steps: list[ValidationStep] = []
        commands = (
            ("git-diff-check", ["git", "diff", "--check"], worktree, 60),
            ("frontend-test", ["npm", "run", "test", "--", "--run"], frontend, 15 * 60),
            ("frontend-build", ["npm", "run", "build"], frontend, 10 * 60),
        )
        try:
            for name, command, cwd, timeout in commands:
                started = time.monotonic()
                try:
                    result = self.run_command(
                        command,
                        cwd=cwd,
                        timeout=timeout,
                        env=_validation_environment(),
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise LoopBlockedError(
                        "BLOCKED_VALIDATION", f"validation step {name} could not finish"
                    ) from exc
                duration = round(time.monotonic() - started, 3)
                steps.append(ValidationStep(name, result.returncode, duration))
                if result.returncode != 0:
                    raise LoopBlockedError(
                        "BLOCKED_VALIDATION",
                        f"validation step {name} failed with exit code {result.returncode}",
                    )
        finally:
            if dependency_link.is_symlink():
                dependency_link.unlink()
            elif dependency_link.exists():
                raise LoopBlockedError(
                    "BLOCKED_VALIDATION_CLEANUP", "temporary dependency link was replaced"
                )
        return ValidationResult(True, tuple(steps))


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
        env=env,
    )


def _validation_environment() -> dict[str, str]:
    allowed = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR")
    environment = {key: os.environ[key] for key in allowed if key in os.environ}
    environment["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    return environment
