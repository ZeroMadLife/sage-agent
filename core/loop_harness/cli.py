"""Command-line control surface for the local Loop Harness."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import stat
import sys
import tempfile
import uuid
from dataclasses import asdict
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from core.loop_harness.artifacts import ArtifactStore
from core.loop_harness.config import LoopConfig
from core.loop_harness.git import GitController
from core.loop_harness.github import GitHubAdapter
from core.loop_harness.logging import configure_logging
from core.loop_harness.manifest import validate_manifest, write_manifest
from core.loop_harness.notifier import CcConnectNotifier
from core.loop_harness.reviewer import CcConnectReviewer
from core.loop_harness.runner import LoopRunner
from core.loop_harness.state import LoopState
from core.loop_harness.worker import CodexWorker


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    config = LoopConfig.from_env()
    config.ensure_local_directories()
    state = LoopState(config.database_path)
    state.initialize()
    logger = configure_logging(config.log_path)

    if args.command == "install":
        install_lease = state.acquire_lease(
            resource="loop-run",
            run_id=f"install-{uuid.uuid4().hex[:12]}",
            owner_id=f"install-{os.getpid()}",
            ttl_seconds=10 * 60,
        )
        try:
            config.validate_static()
            if config.manifest_path.exists() and not args.refresh_manifest:
                validate_manifest(config.controller_root, config.manifest_path)
            else:
                write_manifest(config.controller_root, config.manifest_path)
            launcher = Path(args.launcher).expanduser().resolve()
            _write_launcher(config, launcher)
            state.set_enabled(False, mode="DRY_RUN")
        finally:
            state.release_lease(install_lease)
        print(f"installed: {launcher}")
        return 0

    if args.command == "enable":
        selected = sum((args.dry_run, args.shadow_write, args.pr_canary))
        if selected != 1:
            parser.error("choose exactly one of --dry-run, --shadow-write or --pr-canary")
        config.validate_static()
        validate_manifest(config.controller_root, config.manifest_path)
        if args.pr_canary:
            mode = "PR_CANARY"
        elif args.shadow_write:
            mode = "SHADOW_WRITE"
        else:
            mode = "DRY_RUN"
        state.set_enabled(True, mode=mode)
        print(f"Loop {mode.lower().replace('_', '-')} enabled")
        return 0

    if args.command == "pause":
        state.set_enabled(False, mode="PAUSED_MANUAL")
        print("Loop paused")
        return 0

    if args.command == "status":
        payload = state.status()
        payload["state_bytes"] = _tree_size(config.state_root)
        payload["worktree_bytes"] = _tree_size(config.worktree_root)
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.command == "doctor":
        config.validate_static()
        validate_manifest(config.controller_root, config.manifest_path)
        git = GitController(
            config.repo_root,
            remote=config.remote,
            target_branch=config.target_branch,
        )
        root = git.require_integration_root(
            allow_dirty=state.mode() in {"SHADOW_WRITE", "PR_CANARY"}
        )
        version = CodexWorker(
            codex_bin=config.codex_bin,
            controller_root=config.controller_root,
            reports_root=config.reports_root,
            timeout_seconds=config.run_timeout_seconds,
        ).probe()
        if state.mode() == "PR_CANARY":
            GitHubAdapter(
                gh_bin=config.gh_bin,
                repository=config.github_repository,
                target_branch=config.target_branch,
            ).probe()
            CcConnectReviewer(
                cc_connect_bin=config.cc_connect_bin,
                reports_root=config.reports_root,
            ).probe()
        print(
            f"doctor ok: mode={state.mode()} branch={root.branch} head={root.head_sha} "
            f"dirty_paths={len(root.dirty_paths)} codex={version}"
        )
        return 0

    if args.command == "run":
        report = LoopRunner(config, state, logger=logger).run()
        if args.json:
            print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
        elif report.notification:
            _emit(report.notification, args, config)
        return 0

    if args.command == "digest":
        timezone = ZoneInfo("Asia/Shanghai")
        now = datetime.now(timezone)
        digest_date = args.date or now.date().isoformat()
        day = datetime.fromisoformat(digest_date).date()
        start = datetime.combine(day, time.min, timezone)
        digest_payload = state.digest(
            digest_date=digest_date,
            start_at=start,
            end_at=start + timedelta(days=1),
            force=args.force,
        )
        if digest_payload:
            _emit(digest_payload, args, config)
        return 0

    if args.command == "cleanup":
        cleanup_lease = state.acquire_lease(
            resource="loop-run",
            run_id=f"cleanup-{uuid.uuid4().hex[:12]}",
            owner_id=f"cleanup-{os.getpid()}",
            ttl_seconds=10 * 60,
        )
        try:
            removed = _cleanup_worktrees(config)
            artifact_store = ArtifactStore(config.reports_root)
            removed_artifacts = 0
            for artifact_id, directory in state.expired_artifacts():
                artifact_store.remove(directory)
                state.delete_artifact_record(cleanup_lease, artifact_id)
                removed_artifacts += 1
            counts = state.cleanup()
        finally:
            state.release_lease(cleanup_lease)
        counts["worktrees"] = removed
        counts["artifacts"] = removed_artifacts
        print(json.dumps(counts, ensure_ascii=False, sort_keys=True))
        return 0

    parser.error("unknown command")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sage-loopctl")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install")
    install.add_argument("--refresh-manifest", action="store_true")
    install.add_argument("--launcher", default="~/.local/bin/sage-loopctl")

    enable = subparsers.add_parser("enable")
    enable.add_argument("--dry-run", action="store_true")
    enable.add_argument("--shadow-write", action="store_true")
    enable.add_argument("--pr-canary", action="store_true")
    subparsers.add_parser("pause")
    subparsers.add_parser("status")
    subparsers.add_parser("doctor")

    run = subparsers.add_parser("run")
    run.add_argument("--json", action="store_true")
    _add_notification_options(run)

    digest = subparsers.add_parser("digest")
    digest.add_argument("--date")
    digest.add_argument("--force", action="store_true")
    _add_notification_options(digest)
    subparsers.add_parser("cleanup")
    return parser


def _add_notification_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--notify-project", default="sage")
    parser.add_argument("--notify-session")


def _emit(payload: str, args: argparse.Namespace, config: LoopConfig) -> None:
    if not args.notify_session:
        print(payload)
        return
    CcConnectNotifier(cc_connect_bin=config.cc_connect_bin).send(
        project=args.notify_project,
        session_key=args.notify_session,
        message=payload,
    )


def _write_launcher(config: LoopConfig, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists() and destination.is_symlink():
        raise ValueError("launcher must not be a symlink")
    script = config.controller_root / "scripts/loopctl.py"
    exports = {
        "HOME": str(Path.home()),
        "SAGE_LOOP_REPO_ROOT": str(config.repo_root),
        "SAGE_LOOP_STATE_ROOT": str(config.state_root),
        "SAGE_LOOP_WORKTREE_ROOT": str(config.worktree_root),
        "SAGE_LOOP_CODEX_BIN": str(config.codex_bin),
        "SAGE_LOOP_GH_BIN": str(config.gh_bin),
        "SAGE_LOOP_CC_CONNECT_BIN": str(config.cc_connect_bin),
    }
    lines = ["#!/bin/sh", "set -eu"]
    for key, value in exports.items():
        lines.append(f"export {key}={shlex.quote(value)}")
    lines.append(f'exec {shlex.quote(sys.executable)} {shlex.quote(str(script))} "$@"')
    payload = "\n".join(lines) + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=".sage-loopctl-", dir=destination.parent, text=True
    )
    try:
        os.fchmod(descriptor, 0o700)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        destination.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _cleanup_worktrees(config: LoopConfig) -> int:
    git = GitController(
        config.repo_root,
        remote=config.remote,
        target_branch=config.target_branch,
    )
    removed = 0
    for path in sorted(config.worktree_root.glob("loop-*")):
        if not path.is_dir() or path.is_symlink():
            continue
        git.remove_clean_worktree(path)
        removed += 1
    git.prune_missing_worktrees()
    return removed


def _tree_size(root: Path) -> int:
    if not root.exists():
        return 0
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            total += path.stat().st_size
    return total
