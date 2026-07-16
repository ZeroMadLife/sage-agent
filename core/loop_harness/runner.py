"""Phase 1 deterministic run lifecycle."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import socket
import sqlite3
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from core.loop_harness import POLICY_VERSION
from core.loop_harness.config import LoopConfig
from core.loop_harness.errors import LeaseBusyError, LeaseLostError, LoopBlockedError
from core.loop_harness.git import GitController
from core.loop_harness.manifest import validate_manifest
from core.loop_harness.models import RunReport, WorkerResult
from core.loop_harness.state import Lease, LoopState
from core.loop_harness.worker import CodexWorker

_SCAN_SCOPES = (
    ("core/coding", "tests/core/coding"),
    ("core/knowledge", "tests/core/knowledge"),
    ("api", "tests/api"),
    ("frontend/src",),
    ("agents", "tests/agents"),
    ("mcp_servers", "tests/mcp_servers"),
)
_T = TypeVar("_T")


class LoopRunner:
    def __init__(
        self,
        config: LoopConfig,
        state: LoopState,
        *,
        git: GitController | None = None,
        worker: CodexWorker | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.git = git or GitController(
            config.repo_root,
            remote=config.remote,
            target_branch=config.target_branch,
        )
        self.worker = worker or CodexWorker(
            codex_bin=config.codex_bin,
            controller_root=config.controller_root,
            reports_root=config.reports_root,
            timeout_seconds=config.run_timeout_seconds,
        )
        self.logger = logger or logging.getLogger("sage_loop")

    def run(self) -> RunReport:
        self.state.initialize()
        if not self.state.is_enabled():
            return RunReport("", "SKIPPED", "PAUSED", "Loop is paused")
        run_id = _run_id()
        owner_id = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        try:
            lease = self.state.acquire_lease(
                resource="loop-run",
                run_id=run_id,
                owner_id=owner_id,
                ttl_seconds=self.config.lease_seconds,
            )
        except LeaseBusyError:
            return RunReport(run_id, "SKIPPED", "BUSY", "Another run owns the lease")

        try:
            self.state.begin_run(lease, policy_version=POLICY_VERSION)
        except LeaseLostError:
            return RunReport(
                run_id,
                "BLOCKED",
                "BLOCKED_LEASE_LOST",
                "run lease was lost before initialization",
                notification="Loop 阻断 [BLOCKED_LEASE_LOST]：旧进程已停止",
            )
        except sqlite3.Error:
            with suppress(LeaseLostError):
                self.state.release_lease(lease)
            return RunReport(
                run_id,
                "BLOCKED",
                "BLOCKED_STATE",
                "could not initialize run state",
                notification="Loop 阻断 [BLOCKED_STATE]：无法初始化运行状态",
            )
        worktree: Path | None = None
        base_sha: str | None = None
        terminal_state = "BLOCKED"
        error_code: str | None = None
        summary = "run did not reach a terminal result"
        finding_id: str | None = None
        try:
            self.config.ensure_local_directories()
            self.config.validate_static()
            self._require_disk_capacity()
            manifest = validate_manifest(self.config.controller_root, self.config.manifest_path)
            root_status = self.git.require_clean_integration_root()
            self._fenced(lease, self.worker.probe)

            self._fenced(lease, self.git.fetch)
            base_sha = self.git.remote_sha()
            self.state.set_run_base_sha(lease, base_sha)
            self.git.require_root_at_sha(root_status, base_sha)

            scope = self.state.choose_scan_scope(_SCAN_SCOPES)
            worktree = self.config.worktree_root / run_id
            self._fenced(lease, lambda: self.git.create_detached_worktree(worktree, base_sha))
            protected_digest = hashlib.sha256(
                json.dumps(manifest["files"], sort_keys=True).encode()
            ).hexdigest()
            if (
                validate_manifest(self.config.controller_root, self.config.manifest_path)
                != manifest
            ):
                raise LoopBlockedError(
                    "PAUSED_POLICY_DRIFT", "controlled manifest changed during run"
                )
            result = self._fenced(
                lease,
                lambda: self.worker.run(
                    worktree=worktree,
                    run_id=run_id,
                    base_sha=base_sha,
                    scan_scope=scope,
                    protected_paths_digest=protected_digest,
                ),
            )
            if (
                validate_manifest(self.config.controller_root, self.config.manifest_path)
                != manifest
            ):
                raise LoopBlockedError(
                    "PAUSED_POLICY_DRIFT", "controlled manifest changed during Worker execution"
                )
            result = _enforce_dry_run(result)
            finding_id = self.state.record_worker_result(lease, result)
            self.state.record_scan_scope(lease, scope, base_sha)
            terminal_state, error_code, summary = _terminal_result(result)
        except LoopBlockedError as exc:
            terminal_state = "BLOCKED"
            error_code = exc.code
            summary = str(exc)
        except (OSError, RuntimeError, ValueError) as exc:
            terminal_state = "BLOCKED"
            error_code = "BLOCKED_CONTROLLER"
            summary = f"controller failure: {type(exc).__name__}"
            self.logger.exception("run=%s controller failure", run_id)
        finally:
            if worktree is not None:
                try:
                    self._fenced(lease, lambda: self.git.remove_clean_worktree(worktree))
                except (LoopBlockedError, RuntimeError, LeaseLostError) as exc:
                    terminal_state = "BLOCKED"
                    error_code = getattr(exc, "code", "BLOCKED_WORKTREE_CLEANUP")
                    summary = str(exc)

        try:
            count, paused = self.state.terminalize(
                lease,
                state=terminal_state,
                summary=summary,
                error_code=error_code,
            )
        except LeaseLostError:
            self.logger.error("run=%s lost lease before terminalization", run_id)
            return RunReport(
                run_id,
                "BLOCKED",
                "BLOCKED_LEASE_LOST",
                "run lease was lost before terminalization",
                base_sha,
                "Loop 阻断 [BLOCKED_LEASE_LOST]：旧进程已停止",
            )
        except sqlite3.Error:
            self.logger.exception("run=%s failed to persist terminal state", run_id)
            return RunReport(
                run_id,
                "BLOCKED",
                "BLOCKED_STATE",
                "could not persist terminal run state",
                base_sha,
                "Loop 阻断 [BLOCKED_STATE]：无法保存运行终态",
            )

        notification = _notification(
            terminal_state=terminal_state,
            error_code=error_code,
            summary=summary,
            failure_count=count,
            paused=paused,
            finding_id=finding_id,
        )
        self.logger.info(
            "run=%s state=%s code=%s base=%s",
            run_id,
            terminal_state,
            error_code or "-",
            base_sha or "-",
        )
        return RunReport(
            run_id,
            terminal_state,
            error_code,
            summary,
            base_sha,
            notification,
        )

    def _require_disk_capacity(self) -> None:
        usage = shutil.disk_usage(self.config.state_root)
        if usage.free < self.config.minimum_free_bytes:
            raise LoopBlockedError("BLOCKED_DISK", "less than 2 GiB is available for Loop state")

    def _fenced(self, lease: Lease, operation: Callable[[], _T]) -> _T:
        self.state.assert_lease(lease)
        result = operation()
        self.state.assert_lease(lease)
        return result


def _enforce_dry_run(result: WorkerResult) -> WorkerResult:
    if result.changed_files:
        raise LoopBlockedError(
            "BLOCKED_DRY_RUN_WRITE", "Worker reported file changes during read-only phase"
        )
    if result.verdict != "FIX":
        return result
    return WorkerResult(
        verdict="REPORT",
        summary=f"[dry-run candidate] {result.summary}",
        evidence=result.evidence,
        reproduction=result.reproduction,
        changed_files=(),
        tests=result.tests,
        risk_reasons=(*result.risk_reasons, "Phase 1 forbids code changes"),
        suggested_tier="C",
        confidence=result.confidence,
    )


def _terminal_result(result: WorkerResult) -> tuple[str, str | None, str]:
    if result.verdict == "BLOCKED":
        return "BLOCKED", "BLOCKED_WORKER_REPORTED", result.summary
    if result.verdict == "REPORT":
        return "REPORT", None, result.summary
    return "NO_OP", None, result.summary


def _notification(
    *,
    terminal_state: str,
    error_code: str | None,
    summary: str,
    failure_count: int,
    paused: bool,
    finding_id: str | None,
) -> str | None:
    if terminal_state == "REPORT" and finding_id:
        return f"Loop 发现 {finding_id}：{summary[:180]}"
    if terminal_state != "BLOCKED" or error_code is None:
        return None
    if paused:
        return f"Loop 已自动暂停 [{error_code}]：同类故障连续 {failure_count} 次"
    if failure_count == 1:
        return f"Loop 阻断 [{error_code}]：{summary[:180]}"
    return None


def _run_id() -> str:
    now = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"loop-{now}-{uuid.uuid4().hex[:6]}"
