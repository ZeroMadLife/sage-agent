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
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from core.loop_harness import POLICY_VERSION
from core.loop_harness.artifacts import ArtifactStore
from core.loop_harness.config import LoopConfig
from core.loop_harness.errors import LeaseBusyError, LeaseLostError, LoopBlockedError
from core.loop_harness.git import GitController
from core.loop_harness.manifest import validate_manifest
from core.loop_harness.models import ArtifactReceipt, RunReport, ValidationResult, WorkerResult
from core.loop_harness.policy import classify_candidate, evaluate_diff
from core.loop_harness.state import Lease, LoopState
from core.loop_harness.validation import FrontendValidator
from core.loop_harness.worker import CodexFixer, CodexWorker

_SCAN_SCOPES = (
    ("frontend/src/components/assistant",),
    ("frontend/src/components/coding/chat",),
    ("frontend/src/components/coding/composer",),
    ("frontend/src/components/coding/files",),
    ("frontend/src/components/coding/inspector",),
    ("frontend/src/components/coding/settings",),
    ("frontend/src/components/coding/sidebar",),
    ("frontend/src/views",),
    ("frontend/src/composables",),
    ("core/coding/context", "tests/core/coding"),
    ("core/coding/engine", "tests/core/coding"),
    ("core/coding/memory", "tests/core/coding"),
    ("core/coding/persistence", "tests/core/coding"),
    ("core/coding/tool_executor", "tests/core/coding"),
    ("core/knowledge", "tests/core/knowledge"),
    ("api", "tests/api"),
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
        fixer: CodexFixer | None = None,
        validator: FrontendValidator | None = None,
        artifact_store: ArtifactStore | None = None,
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
            timeout_seconds=config.scanner_timeout_seconds,
        )
        self.fixer = fixer or CodexFixer(
            codex_bin=config.codex_bin,
            controller_root=config.controller_root,
            reports_root=config.reports_root,
            timeout_seconds=config.fixer_timeout_seconds,
        )
        self.validator = validator or FrontendValidator(repo_root=config.repo_root)
        self.artifact_store = artifact_store or ArtifactStore(config.reports_root)
        self.logger = logger or logging.getLogger("sage_loop")

    def run(self) -> RunReport:
        self.state.initialize()
        if not self.state.is_enabled():
            return RunReport("", "SKIPPED", "PAUSED", "Loop is paused")
        mode = self.state.mode()
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
            self.state.begin_run(
                lease,
                policy_version=POLICY_VERSION,
                mode=mode,
                target_branch=self.config.target_branch,
            )
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
            if mode == "DRY_RUN":
                root_status = self.git.require_clean_integration_root()
            elif mode == "SHADOW_WRITE":
                root_status = self.git.require_integration_root(allow_dirty=True)
            else:
                raise LoopBlockedError("PAUSED_MODE", f"unsupported active mode: {mode}")
            self._fenced(lease, self.worker.probe, expected_mode=mode)

            self._fenced(lease, self.git.fetch, expected_mode=mode)
            base_sha = self._fenced(lease, self.git.remote_sha, expected_mode=mode)
            self.state.set_run_base_sha(lease, base_sha)
            if mode == "DRY_RUN":
                self.git.require_root_at_sha(root_status, base_sha)
            else:
                human_paths = self._fenced(
                    lease,
                    lambda: self.git.human_change_paths(root_status, base_sha),
                    expected_mode=mode,
                )
                root_status = replace(
                    root_status,
                    dirty=bool(human_paths),
                    dirty_paths=human_paths,
                )

            scope = self.state.choose_scan_scope(_SCAN_SCOPES)
            worktree = self.config.worktree_root / run_id
            self._fenced(
                lease,
                lambda: self.git.create_detached_worktree(worktree, base_sha),
                expected_mode=mode,
            )
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
                    phase="DRY_RUN_READ_ONLY" if mode == "DRY_RUN" else "SHADOW_SCAN",
                ),
                expected_mode=mode,
            )
            if (
                validate_manifest(self.config.controller_root, self.config.manifest_path)
                != manifest
            ):
                raise LoopBlockedError(
                    "PAUSED_POLICY_DRIFT", "controlled manifest changed during Worker execution"
                )
            if mode == "DRY_RUN":
                result = _enforce_dry_run(result)
                finding_id = self.state.record_worker_result(
                    lease,
                    result,
                    mode=mode,
                    target_branch=self.config.target_branch,
                    base_sha=base_sha,
                )
                self.state.record_scan_scope(lease, scope, base_sha)
                terminal_state, error_code, summary = _terminal_result(result)
            else:
                scanner_diff = self._fenced(
                    lease,
                    lambda: self.git.diff_snapshot(worktree, base_sha=base_sha),
                    expected_mode=mode,
                )
                if scanner_diff.changed_files:
                    raise LoopBlockedError(
                        "BLOCKED_SCANNER_WRITE", "read-only Scanner modified the worktree"
                    )
                decision = classify_candidate(
                    result,
                    dirty_paths=root_status.dirty_paths,
                    scan_scope=scope,
                )
                if result.verdict in {"FIX", "FRONTEND_CANDIDATE"} and not decision.allowed:
                    result = _downgrade_candidate(result, decision.reasons)
                finding_id = self.state.record_worker_result(
                    lease,
                    result,
                    mode=mode,
                    target_branch=self.config.target_branch,
                    base_sha=base_sha,
                )
                self.state.record_scan_scope(lease, scope, base_sha)
                if result.verdict not in {"FIX", "FRONTEND_CANDIDATE"}:
                    terminal_state, error_code, summary = _terminal_result(result)
                else:
                    fixer_result = self._fenced(
                        lease,
                        lambda: self.fixer.run(
                            worktree=worktree,
                            run_id=run_id,
                            base_sha=base_sha,
                            allowed_paths=result.changed_files,
                            dirty_paths=root_status.dirty_paths,
                            protected_paths_digest=protected_digest,
                        ),
                        expected_mode=mode,
                    )
                    if (
                        validate_manifest(self.config.controller_root, self.config.manifest_path)
                        != manifest
                    ):
                        raise LoopBlockedError(
                            "PAUSED_POLICY_DRIFT",
                            "controlled manifest changed during Fixer execution",
                        )
                    self._fenced(lease, self.git.fetch, expected_mode=mode)
                    current_sha = self._fenced(
                        lease, self.git.remote_sha, expected_mode=mode
                    )
                    if current_sha != base_sha:
                        raise LoopBlockedError(
                            "BLOCKED_BASE_DRIFT", "target branch changed during Fixer execution"
                        )
                    snapshot = self._fenced(
                        lease,
                        lambda: self.git.diff_snapshot(worktree, base_sha=base_sha),
                        expected_mode=mode,
                    )
                    if set(fixer_result.changed_files) != set(snapshot.changed_files):
                        raise LoopBlockedError(
                            "BLOCKED_FIXER_OUTPUT",
                            "Fixer reported files do not match the actual diff",
                        )
                    diff_decision = evaluate_diff(
                        result,
                        snapshot,
                        dirty_paths=root_status.dirty_paths,
                    )
                    if not diff_decision.allowed:
                        raise LoopBlockedError(
                            "BLOCKED_DIFF_POLICY", "; ".join(diff_decision.reasons)
                        )
                    validation = self._fenced(
                        lease,
                        lambda: self.validator.validate(worktree),
                        expected_mode=mode,
                    )
                    validated_snapshot = self._fenced(
                        lease,
                        lambda: self.git.diff_snapshot(worktree, base_sha=base_sha),
                        expected_mode=mode,
                    )
                    if validated_snapshot != snapshot:
                        raise LoopBlockedError(
                            "BLOCKED_VALIDATION_MUTATION",
                            "validation changed the candidate diff",
                        )
                    self._fenced(lease, self.git.fetch, expected_mode=mode)
                    validated_base_sha = self._fenced(
                        lease, self.git.remote_sha, expected_mode=mode
                    )
                    if validated_base_sha != base_sha:
                        raise LoopBlockedError(
                            "BLOCKED_BASE_DRIFT", "target branch changed during validation"
                        )
                    patch = self._fenced(
                        lease,
                        lambda: self.git.diff_patch(worktree, base_sha=base_sha),
                        expected_mode=mode,
                    )
                    artifact = self._save_shadow_artifact(
                        lease,
                        mode=mode,
                        run_id=run_id,
                        patch=patch,
                        validation=validation,
                    )
                    try:
                        self.state.record_shadow_result(
                            lease,
                            result,
                            snapshot,
                            validation,
                            artifact,
                            tier=diff_decision.tier,
                            target_branch=self.config.target_branch,
                        )
                    except sqlite3.Error:
                        self.artifact_store.remove(artifact.directory)
                        raise
                    terminal_state = "SHADOW_VALIDATED"
                    error_code = None
                    summary = fixer_result.summary
        except LoopBlockedError as exc:
            terminal_state = "BLOCKED"
            error_code = exc.code
            summary = str(exc)
        except sqlite3.Error:
            terminal_state = "BLOCKED"
            error_code = "BLOCKED_STATE"
            summary = "could not persist Loop state"
            self.logger.exception("run=%s state persistence failure", run_id)
        except (OSError, RuntimeError, ValueError) as exc:
            terminal_state = "BLOCKED"
            error_code = "BLOCKED_CONTROLLER"
            summary = f"controller failure: {type(exc).__name__}"
            self.logger.exception("run=%s controller failure", run_id)
        finally:
            if worktree is not None:
                try:
                    if mode == "SHADOW_WRITE":
                        self._fenced(
                            lease,
                            lambda: self.git.remove_managed_worktree(
                                worktree, discard_changes=True
                            ),
                        )
                    else:
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

    def _fenced(
        self,
        lease: Lease,
        operation: Callable[[], _T],
        *,
        expected_mode: str | None = None,
    ) -> _T:
        self.state.assert_lease(lease)
        if expected_mode is not None:
            self._require_active_mode(expected_mode)
        result = operation()
        self.state.assert_lease(lease)
        if expected_mode is not None:
            self._require_active_mode(expected_mode)
        return result

    def _require_active_mode(self, expected_mode: str) -> None:
        if not self.state.is_enabled() or self.state.mode() != expected_mode:
            raise LoopBlockedError(
                "BLOCKED_MODE_CHANGED", "Loop mode changed while the run was active"
            )

    def _save_shadow_artifact(
        self,
        lease: Lease,
        *,
        mode: str,
        run_id: str,
        patch: str,
        validation: ValidationResult,
    ) -> ArtifactReceipt:
        self.state.assert_lease(lease)
        self._require_active_mode(mode)
        artifact = self.artifact_store.save_shadow(
            run_id=run_id,
            patch=patch,
            validation=validation,
        )
        try:
            self.state.assert_lease(lease)
            self._require_active_mode(mode)
        except (LeaseLostError, LoopBlockedError):
            self.artifact_store.remove(artifact.directory)
            raise
        return artifact


def _enforce_dry_run(result: WorkerResult) -> WorkerResult:
    if result.changed_files:
        raise LoopBlockedError(
            "BLOCKED_DRY_RUN_WRITE", "Worker reported file changes during read-only phase"
        )
    if result.verdict not in {"FIX", "FRONTEND_CANDIDATE"}:
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


def _downgrade_candidate(result: WorkerResult, reasons: tuple[str, ...]) -> WorkerResult:
    return WorkerResult(
        verdict="REPORT",
        summary=f"[shadow report] {result.summary}",
        evidence=result.evidence,
        reproduction=result.reproduction,
        changed_files=(),
        tests=result.tests,
        risk_reasons=(*result.risk_reasons, *reasons),
        suggested_tier="C",
        confidence=result.confidence,
    )


def _terminal_result(result: WorkerResult) -> tuple[str, str | None, str]:
    if result.verdict == "BLOCKED":
        return "BLOCKED", "BLOCKED_WORKER_REPORTED", result.summary
    if result.verdict == "REPORT":
        return "REPORT", None, result.summary
    if result.verdict in {"FIX", "FRONTEND_CANDIDATE"}:
        return "BLOCKED", "BLOCKED_UNHANDLED_CANDIDATE", result.summary
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
    if terminal_state == "SHADOW_VALIDATED":
        return f"Loop shadow 已验证：{summary[:180]}"
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
