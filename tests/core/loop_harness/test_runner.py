from __future__ import annotations

import subprocess

from core.loop_harness.config import LoopConfig
from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.git import RootStatus
from core.loop_harness.manifest import write_manifest
from core.loop_harness.models import WorkerResult
from core.loop_harness.runner import LoopRunner
from core.loop_harness.state import LoopState


class FakeGit:
    def __init__(self, *, blocked: bool = False) -> None:
        self.blocked = blocked
        self.created = 0
        self.removed = 0

    def require_clean_integration_root(self) -> RootStatus:
        if self.blocked:
            raise LoopBlockedError("BLOCKED_ROOT_DIRTY", "human work is present")
        return RootStatus("dev/sage-v7", "a" * 40, False)

    def fetch(self) -> None:
        return None

    def remote_sha(self) -> str:
        return "a" * 40

    def require_root_at_sha(self, status: RootStatus, remote_sha: str) -> None:
        assert status.head_sha == remote_sha

    def create_detached_worktree(self, destination, base_sha: str) -> None:
        assert base_sha == "a" * 40
        destination.mkdir(parents=True)
        self.created += 1

    def remove_clean_worktree(self, destination) -> None:
        destination.rmdir()
        self.removed += 1


class FakeWorker:
    def __init__(self, result: WorkerResult) -> None:
        self.result = result
        self.calls = 0

    def probe(self) -> str:
        return "codex-cli test"

    def run(self, **kwargs) -> WorkerResult:
        self.calls += 1
        return self.result


def _git(root, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _config(tmp_path) -> LoopConfig:
    controller = tmp_path / "controller"
    (controller / "docs/loop-harness").mkdir(parents=True)
    (controller / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    (controller / "docs/loop-harness/POLICY.md").write_text("policy\n", encoding="utf-8")
    _git(controller, "init")
    _git(controller, "config", "user.name", "Loop Test")
    _git(controller, "config", "user.email", "loop@example.com")
    _git(controller, "add", ".")
    _git(controller, "commit", "-m", "controller")
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    config = LoopConfig(
        repo_root=repo,
        controller_root=controller,
        state_root=tmp_path / "state",
        worktree_root=tmp_path / "worktrees",
        codex_bin=tmp_path / "codex",
        minimum_free_bytes=0,
    )
    config.codex_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    config.codex_bin.chmod(0o700)
    config.ensure_local_directories()
    write_manifest(controller, config.manifest_path)
    return config


def _result(verdict: str = "NO_OP") -> WorkerResult:
    return WorkerResult(
        verdict=verdict,
        summary="deterministic result",
        evidence=("evidence",) if verdict != "NO_OP" else (),
        reproduction=(),
        changed_files=(),
        tests=(),
        risk_reasons=(),
        suggested_tier="C",
        confidence=0.9,
    )


def test_runner_terminalizes_noop_and_cleans_worktree(tmp_path) -> None:
    config = _config(tmp_path)
    state = LoopState(config.database_path)
    state.initialize()
    state.set_enabled(True)
    git = FakeGit()
    worker = FakeWorker(_result())

    report = LoopRunner(config, state, git=git, worker=worker).run()

    assert report.state == "NO_OP"
    assert report.notification is None
    assert git.created == git.removed == 1
    assert worker.calls == 1
    assert list(config.worktree_root.iterdir()) == []
    assert list(config.reports_root.iterdir()) == []


def test_runner_downgrades_fix_to_report_without_writing(tmp_path) -> None:
    config = _config(tmp_path)
    state = LoopState(config.database_path)
    state.initialize()
    state.set_enabled(True)

    report = LoopRunner(
        config,
        state,
        git=FakeGit(),
        worker=FakeWorker(_result("FIX")),
    ).run()

    assert report.state == "REPORT"
    assert report.error_code is None
    assert report.notification is not None
    assert "dry-run candidate" in report.notification


def test_runner_pauses_after_three_root_dirty_failures(tmp_path) -> None:
    config = _config(tmp_path)
    state = LoopState(config.database_path)
    state.initialize()
    state.set_enabled(True)
    runner = LoopRunner(
        config,
        state,
        git=FakeGit(blocked=True),
        worker=FakeWorker(_result()),
    )

    first = runner.run()
    second = runner.run()
    third = runner.run()

    assert first.notification and "BLOCKED_ROOT_DIRTY" in first.notification
    assert second.notification is None
    assert third.notification and "自动暂停" in third.notification
    assert state.is_enabled() is False
    assert state.status()["consecutive_error_count"] == 3
