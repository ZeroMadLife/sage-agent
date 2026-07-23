"""SQLite canonical state, leases, fencing, and digest aggregation."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from core.loop_harness.errors import LeaseBusyError, LeaseLostError
from core.loop_harness.models import (
    ArtifactReceipt,
    DiffSnapshot,
    PullRequestReceipt,
    ReviewerResult,
    ValidationResult,
    WorkerResult,
)

_SUPPORTED_MODES = {
    "DRY_RUN",
    "SHADOW_WRITE",
    "PR_CANARY",
    "AUTO_MERGE_TIER_A",
    "PAUSED_MANUAL",
    "PAUSED_ERROR",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fence_state (
    resource TEXT PRIMARY KEY,
    next_token INTEGER NOT NULL CHECK (next_token >= 1)
);
CREATE TABLE IF NOT EXISTS leases (
    resource TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
    acquired_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    base_sha TEXT,
    policy_version TEXT NOT NULL,
    state TEXT NOT NULL,
    error_code TEXT,
    summary TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    mode TEXT NOT NULL DEFAULT 'DRY_RUN',
    target_branch TEXT
);
CREATE INDEX IF NOT EXISTS runs_started_idx ON runs(started_at);
CREATE TABLE IF NOT EXISTS scan_cursors (
    module TEXT PRIMARY KEY,
    base_sha TEXT NOT NULL,
    last_scanned_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS candidates (
    fingerprint TEXT PRIMARY KEY,
    verdict TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL,
    cooldown_until TEXT,
    mode TEXT NOT NULL DEFAULT 'DRY_RUN',
    target_branch TEXT,
    base_sha TEXT,
    lifecycle_state TEXT NOT NULL DEFAULT 'DISCOVERED',
    suggested_tier TEXT,
    changed_files_json TEXT NOT NULL DEFAULT '[]',
    tests_json TEXT NOT NULL DEFAULT '[]',
    risk_reasons_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL,
    diff_json TEXT
);
CREATE TABLE IF NOT EXISTS findings (
    finding_id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    report_ref TEXT
);
CREATE TABLE IF NOT EXISTS pull_requests (
    number INTEGER PRIMARY KEY,
    run_id TEXT,
    branch TEXT,
    url TEXT,
    base_sha TEXT,
    head_sha TEXT NOT NULL,
    tier TEXT NOT NULL,
    state TEXT NOT NULL,
    review_verdict TEXT,
    review_summary TEXT,
    created_day TEXT,
    created_at TEXT,
    merged_sha TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_digests (
    digest_date TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    emitted_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class Lease:
    resource: str
    run_id: str
    owner_id: str
    fencing_token: int
    expires_at: float


class LoopState:
    """Own all durable controller decisions in one local SQLite database."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._validate_path()

    def initialize(self) -> None:
        self._validate_path()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self._connect() as connection:
            connection.executescript(_SCHEMA)
            connection.execute("BEGIN IMMEDIATE")
            try:
                _migrate_schema(connection)
                connection.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES ('enabled', '0')"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES ('mode', 'DRY_RUN')"
                )
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()
        os.chmod(self.path, 0o600)

    def acquire_lease(
        self,
        *,
        resource: str,
        run_id: str,
        owner_id: str,
        ttl_seconds: int,
        now: datetime | None = None,
    ) -> Lease:
        if ttl_seconds < 1:
            raise ValueError("lease ttl must be positive")
        timestamp = _timestamp(now)
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT run_id, expires_at FROM leases WHERE resource = ?", (resource,)
            ).fetchone()
            if row is not None and float(row["expires_at"]) > timestamp:
                raise LeaseBusyError(f"resource {resource} is owned by {row['run_id']}")
            connection.execute("DELETE FROM leases WHERE resource = ?", (resource,))
            connection.execute(
                "INSERT OR IGNORE INTO fence_state(resource, next_token) VALUES (?, 1)",
                (resource,),
            )
            token_row = connection.execute(
                "SELECT next_token FROM fence_state WHERE resource = ?", (resource,)
            ).fetchone()
            if token_row is None:
                raise sqlite3.DatabaseError("missing fence state")
            token = int(token_row["next_token"])
            connection.execute(
                "UPDATE fence_state SET next_token = ? WHERE resource = ?",
                (token + 1, resource),
            )
            expires_at = timestamp + ttl_seconds
            connection.execute(
                "INSERT INTO leases(resource, run_id, owner_id, fencing_token, acquired_at, "
                "expires_at) VALUES (?, ?, ?, ?, ?, ?)",
                (resource, run_id, owner_id, token, timestamp, expires_at),
            )
        return Lease(resource, run_id, owner_id, token, expires_at)

    def assert_lease(self, lease: Lease, *, now: datetime | None = None) -> None:
        timestamp = _timestamp(now)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM leases WHERE resource = ? AND run_id = ? AND owner_id = ? "
                "AND fencing_token = ? AND expires_at > ?",
                (
                    lease.resource,
                    lease.run_id,
                    lease.owner_id,
                    lease.fencing_token,
                    timestamp,
                ),
            ).fetchone()
        if row is None:
            raise LeaseLostError(f"lease lost for {lease.run_id}")

    def begin_run(
        self,
        lease: Lease,
        *,
        policy_version: str,
        mode: str = "DRY_RUN",
        target_branch: str | None = None,
    ) -> None:
        with self._transaction() as connection:
            self._assert_lease_row(connection, lease)
            connection.execute(
                "INSERT INTO runs(run_id, policy_version, state, started_at, mode, target_branch) "
                "VALUES (?, ?, 'RUNNING', ?, ?, ?)",
                (lease.run_id, policy_version, _iso_now(), mode, target_branch),
            )

    def set_run_base_sha(self, lease: Lease, base_sha: str) -> None:
        if len(base_sha) != 40 or any(char not in "0123456789abcdef" for char in base_sha):
            raise ValueError("base SHA must be a full lowercase Git SHA")
        with self._transaction() as connection:
            self._assert_lease_row(connection, lease)
            connection.execute(
                "UPDATE runs SET base_sha = ? WHERE run_id = ?",
                (base_sha, lease.run_id),
            )

    def terminalize(
        self,
        lease: Lease,
        *,
        state: str,
        summary: str,
        error_code: str | None = None,
    ) -> tuple[int, bool]:
        """Persist a terminal state and release the lease in one transaction."""
        with self._transaction() as connection:
            self._assert_lease_row(connection, lease)
            cursor = connection.execute(
                "UPDATE runs SET state = ?, error_code = ?, summary = ?, finished_at = ? "
                "WHERE run_id = ? AND state = 'RUNNING'",
                (state, error_code, _bounded(summary, 1000), _iso_now(), lease.run_id),
            )
            if cursor.rowcount != 1:
                raise LeaseLostError(f"run {lease.run_id} is not terminalizable")
            count, paused = _register_outcome_row(
                connection, error_code if state == "BLOCKED" else None
            )
            connection.execute(
                "DELETE FROM leases WHERE resource = ? AND run_id = ? AND owner_id = ? "
                "AND fencing_token = ?",
                (lease.resource, lease.run_id, lease.owner_id, lease.fencing_token),
            )
            return count, paused

    def release_lease(self, lease: Lease) -> None:
        with self._transaction() as connection:
            self._assert_lease_row(connection, lease)
            connection.execute(
                "DELETE FROM leases WHERE resource = ? AND run_id = ? AND owner_id = ? "
                "AND fencing_token = ?",
                (lease.resource, lease.run_id, lease.owner_id, lease.fencing_token),
            )

    def record_worker_result(
        self,
        lease: Lease,
        result: WorkerResult,
        *,
        mode: str = "DRY_RUN",
        target_branch: str | None = None,
        base_sha: str | None = None,
    ) -> str | None:
        now = _iso_now()
        evidence_json = json.dumps(result.evidence, ensure_ascii=False)
        fingerprint = _fingerprint(
            result.summary,
            result.evidence,
            changed_files=result.changed_files,
            target_branch=target_branch,
        )
        with self._transaction() as connection:
            self._assert_lease_row(connection, lease)
            connection.execute(
                "INSERT INTO candidates(fingerprint, verdict, summary, evidence_json, "
                "first_seen_at, last_seen_at, occurrence_count, mode, target_branch, base_sha, "
                "lifecycle_state, suggested_tier, changed_files_json, tests_json, "
                "risk_reasons_json, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'DISCOVERED', ?, ?, ?, ?, ?) "
                "ON CONFLICT(fingerprint) DO UPDATE SET verdict = excluded.verdict, "
                "summary = excluded.summary, evidence_json = excluded.evidence_json, "
                "last_seen_at = excluded.last_seen_at, "
                "occurrence_count = candidates.occurrence_count + 1, mode = excluded.mode, "
                "target_branch = excluded.target_branch, base_sha = excluded.base_sha, "
                "lifecycle_state = 'DISCOVERED', diff_json = NULL, "
                "suggested_tier = excluded.suggested_tier, "
                "changed_files_json = excluded.changed_files_json, tests_json = excluded.tests_json, "
                "risk_reasons_json = excluded.risk_reasons_json, confidence = excluded.confidence",
                (
                    fingerprint,
                    result.verdict,
                    _bounded(result.summary, 500),
                    evidence_json,
                    now,
                    now,
                    mode,
                    target_branch,
                    base_sha,
                    result.suggested_tier,
                    json.dumps(result.changed_files, ensure_ascii=False),
                    json.dumps(result.tests, ensure_ascii=False),
                    json.dumps(result.risk_reasons, ensure_ascii=False),
                    result.confidence,
                ),
            )
            if result.verdict != "REPORT" and not (result.verdict == "FIX" and mode == "DRY_RUN"):
                return None
            finding_id = f"LH-{now[:10].replace('-', '')}-{fingerprint[:6].upper()}"
            connection.execute(
                "INSERT INTO findings(finding_id, fingerprint, status, summary, evidence_json, "
                "created_at, updated_at) VALUES (?, ?, 'OPEN', ?, ?, ?, ?) "
                "ON CONFLICT(fingerprint) DO UPDATE SET updated_at = excluded.updated_at",
                (
                    finding_id,
                    fingerprint,
                    _bounded(result.summary, 500),
                    evidence_json,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT finding_id FROM findings WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            return str(row["finding_id"]) if row is not None else finding_id

    def record_shadow_result(
        self,
        lease: Lease,
        candidate: WorkerResult,
        snapshot: DiffSnapshot,
        validation: ValidationResult,
        artifact: ArtifactReceipt,
        *,
        tier: str,
        target_branch: str | None = None,
    ) -> None:
        fingerprint = _fingerprint(
            candidate.summary,
            candidate.evidence,
            changed_files=candidate.changed_files,
            target_branch=target_branch,
        )
        diff_json = json.dumps(
            {
                "changed_files": snapshot.changed_files,
                "additions": snapshot.additions,
                "deletions": snapshot.deletions,
                "behavior_changed": snapshot.behavior_changed,
                "validation": [
                    {
                        "name": step.name,
                        "exit_code": step.exit_code,
                        "duration_seconds": step.duration_seconds,
                    }
                    for step in validation.steps
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        with self._transaction() as connection:
            self._assert_lease_row(connection, lease)
            cursor = connection.execute(
                "UPDATE candidates SET lifecycle_state = 'SHADOW_VALIDATED', "
                "suggested_tier = ?, diff_json = ?, last_seen_at = ? WHERE fingerprint = ?",
                (tier, diff_json, _iso_now(), fingerprint),
            )
            if cursor.rowcount != 1:
                raise sqlite3.DatabaseError("shadow candidate was not recorded")
            now = datetime.now(UTC)
            connection.execute(
                "INSERT INTO artifacts(artifact_id, run_id, path, sha256, size_bytes, "
                "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"artifact-{lease.run_id}",
                    lease.run_id,
                    str(artifact.directory),
                    artifact.sha256,
                    artifact.size_bytes,
                    _utc_iso(now),
                    _utc_iso(now + timedelta(days=7)),
                ),
            )

    def require_pr_capacity(self, lease: Lease, *, now: datetime | None = None) -> str:
        local = (now or datetime.now(UTC)).astimezone(ZoneInfo("Asia/Shanghai"))
        created_day = local.date().isoformat()
        with self._transaction() as connection:
            self._assert_lease_row(connection, lease)
            open_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM pull_requests WHERE state IN "
                    "('DRAFT', 'REVIEWING', 'DRAFT_REVIEWED', 'CHANGES_REQUESTED')"
                ).fetchone()[0]
            )
            if open_count:
                raise LeaseBusyError("an open Loop PR already exists")
            daily_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM pull_requests WHERE created_day = ?",
                    (created_day,),
                ).fetchone()[0]
            )
            if daily_count:
                raise LeaseBusyError("daily Loop PR quota is exhausted")
        return created_day

    def record_pull_request(
        self,
        lease: Lease,
        receipt: PullRequestReceipt,
        *,
        base_sha: str,
        tier: str,
        created_day: str,
    ) -> None:
        now = _iso_now()
        with self._transaction() as connection:
            self._assert_lease_row(connection, lease)
            existing = connection.execute(
                "SELECT number, head_sha FROM pull_requests WHERE branch = ?",
                (receipt.branch,),
            ).fetchone()
            if existing is not None and (
                int(existing["number"]) != receipt.number
                or str(existing["head_sha"]) != receipt.head_sha
            ):
                raise sqlite3.DatabaseError("PR branch idempotency conflict")
            connection.execute(
                "INSERT INTO pull_requests(number, run_id, branch, url, base_sha, head_sha, "
                "tier, state, created_day, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?) "
                "ON CONFLICT(number) DO UPDATE SET url = excluded.url, "
                "updated_at = excluded.updated_at",
                (
                    receipt.number,
                    lease.run_id,
                    receipt.branch,
                    receipt.url,
                    base_sha,
                    receipt.head_sha,
                    tier,
                    created_day,
                    now,
                    now,
                ),
            )

    def record_review_result(
        self,
        lease: Lease,
        *,
        pr_number: int,
        head_sha: str,
        result: ReviewerResult,
    ) -> None:
        state = {
            "PASS": "DRAFT_REVIEWED",
            "REQUEST_CHANGES": "CHANGES_REQUESTED",
            "BLOCK": "CHANGES_REQUESTED",
        }[result.verdict]
        with self._transaction() as connection:
            self._assert_lease_row(connection, lease)
            cursor = connection.execute(
                "UPDATE pull_requests SET state = ?, review_verdict = ?, review_summary = ?, "
                "updated_at = ? WHERE number = ? AND head_sha = ?",
                (
                    state,
                    result.verdict,
                    _bounded(result.summary, 1000),
                    _iso_now(),
                    pr_number,
                    head_sha,
                ),
            )
            if cursor.rowcount != 1:
                raise sqlite3.DatabaseError("PR review head does not match durable state")

    def record_merge_result(
        self,
        lease: Lease,
        *,
        pr_number: int,
        head_sha: str,
        merged_sha: str,
    ) -> None:
        if len(merged_sha) != 40 or any(char not in "0123456789abcdef" for char in merged_sha):
            raise ValueError("merge SHA must be a full lowercase Git SHA")
        with self._transaction() as connection:
            self._assert_lease_row(connection, lease)
            cursor = connection.execute(
                "UPDATE pull_requests SET state = 'MERGED', merged_sha = ?, updated_at = ? "
                "WHERE number = ? AND head_sha = ? AND tier = 'A' "
                "AND review_verdict = 'PASS'",
                (merged_sha, _iso_now(), pr_number, head_sha),
            )
            if cursor.rowcount != 1:
                raise sqlite3.DatabaseError("merged PR does not satisfy durable Tier A gates")

    def set_enabled(self, enabled: bool, *, mode: str = "DRY_RUN") -> None:
        if mode not in _SUPPORTED_MODES:
            raise ValueError(f"unsupported Loop mode: {mode}")
        if enabled and mode.startswith("PAUSED_"):
            raise ValueError("enabled Loop cannot use a paused mode")
        with self._transaction() as connection:
            _set_setting(connection, "enabled", "1" if enabled else "0")
            _set_setting(connection, "mode", mode)
            if enabled:
                _set_setting(connection, "consecutive_error_code", "")
                _set_setting(connection, "consecutive_error_count", "0")

    def choose_scan_scope(self, scopes: tuple[tuple[str, ...], ...]) -> tuple[str, ...]:
        if not scopes or any(not scope for scope in scopes):
            raise ValueError("scan scopes must not be empty")
        with self._connect() as connection:
            timestamps = {
                str(row["module"]): str(row["last_scanned_at"])
                for row in connection.execute("SELECT module, last_scanned_at FROM scan_cursors")
            }
        return min(scopes, key=lambda scope: timestamps.get(scope[0], ""))

    def record_scan_scope(self, lease: Lease, scope: tuple[str, ...], base_sha: str) -> None:
        if not scope:
            raise ValueError("scan scope must not be empty")
        with self._transaction() as connection:
            self._assert_lease_row(connection, lease)
            connection.execute(
                "INSERT INTO scan_cursors(module, base_sha, last_scanned_at) VALUES (?, ?, ?) "
                "ON CONFLICT(module) DO UPDATE SET base_sha = excluded.base_sha, "
                "last_scanned_at = excluded.last_scanned_at",
                (scope[0], base_sha, _iso_now()),
            )

    def is_enabled(self) -> bool:
        return self.setting("enabled", "0") == "1"

    def mode(self) -> str:
        return self.setting("mode", "DRY_RUN")

    def setting(self, key: str, default: str = "") -> str:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row is not None else default

    def status(self) -> dict[str, Any]:
        with self._connect() as connection:
            settings = {
                str(row["key"]): str(row["value"])
                for row in connection.execute("SELECT key, value FROM settings")
            }
            lease = connection.execute(
                "SELECT resource, run_id, owner_id, fencing_token, expires_at FROM leases "
                "ORDER BY acquired_at LIMIT 1"
            ).fetchone()
            latest = connection.execute(
                "SELECT run_id, base_sha, state, error_code, summary, started_at, finished_at "
                "FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            open_findings = int(
                connection.execute(
                    "SELECT COUNT(*) FROM findings WHERE status = 'OPEN'"
                ).fetchone()[0]
            )
            shadow_validated = int(
                connection.execute(
                    "SELECT COUNT(*) FROM candidates WHERE lifecycle_state = 'SHADOW_VALIDATED'"
                ).fetchone()[0]
            )
            artifact_bytes = int(
                connection.execute("SELECT COALESCE(SUM(size_bytes), 0) FROM artifacts").fetchone()[
                    0
                ]
            )
            open_pull_requests = int(
                connection.execute(
                    "SELECT COUNT(*) FROM pull_requests WHERE state IN "
                    "('DRAFT', 'REVIEWING', 'DRAFT_REVIEWED', 'CHANGES_REQUESTED')"
                ).fetchone()[0]
            )
        return {
            "enabled": settings.get("enabled", "0") == "1",
            "mode": settings.get("mode", "DRY_RUN"),
            "consecutive_error_code": settings.get("consecutive_error_code") or None,
            "consecutive_error_count": int(settings.get("consecutive_error_count", "0")),
            "lease": dict(lease) if lease is not None else None,
            "latest_run": dict(latest) if latest is not None else None,
            "open_findings": open_findings,
            "shadow_validated_candidates": shadow_validated,
            "artifact_bytes": artifact_bytes,
            "open_pull_requests": open_pull_requests,
        }

    def digest(
        self,
        *,
        digest_date: str,
        start_at: datetime,
        end_at: datetime,
        force: bool = False,
    ) -> str | None:
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT payload FROM daily_digests WHERE digest_date = ?", (digest_date,)
            ).fetchone()
            if existing is not None and not force:
                return None
            rows = connection.execute(
                "SELECT state, error_code FROM runs WHERE started_at >= ? AND started_at < ?",
                (_utc_iso(start_at), _utc_iso(end_at)),
            ).fetchall()
            counts: dict[str, int] = {}
            for row in rows:
                state = str(row["state"])
                counts[state] = counts.get(state, 0) + 1
            findings = int(
                connection.execute(
                    "SELECT COUNT(*) FROM findings WHERE created_at >= ? AND created_at < ?",
                    (_utc_iso(start_at), _utc_iso(end_at)),
                ).fetchone()[0]
            )
            enabled = _get_setting(connection, "enabled", "0") == "1"
            mode = _get_setting(connection, "mode", "DRY_RUN")
            payload = (
                f"Loop 日报 {digest_date[5:]}\n"
                f"扫描：{len(rows)} 次，{counts.get('NO_OP', 0)} 无问题，"
                f"{counts.get('SKIPPED', 0)} 跳过，{counts.get('BLOCKED', 0)} 异常\n"
                f"大范围发现：{findings}\n"
                f"状态：{mode if enabled else '已暂停'}"
            )
            payload_hash = hashlib.sha256(payload.encode()).hexdigest()
            connection.execute(
                "INSERT INTO daily_digests(digest_date, payload, payload_hash, emitted_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(digest_date) DO UPDATE SET "
                "payload = excluded.payload, payload_hash = excluded.payload_hash, "
                "emitted_at = excluded.emitted_at",
                (digest_date, payload, payload_hash, _iso_now()),
            )
            return payload

    def cleanup(self, *, now: datetime | None = None) -> dict[str, int]:
        reference = now or datetime.now(UTC)
        closed_cutoff = _utc_iso(reference - timedelta(days=90))
        run_cutoff = _utc_iso(reference - timedelta(days=90))
        with self._transaction() as connection:
            candidates = connection.execute(
                "DELETE FROM candidates WHERE last_seen_at < ? AND fingerprint NOT IN "
                "(SELECT fingerprint FROM findings WHERE status = 'OPEN')",
                (closed_cutoff,),
            ).rowcount
            runs = connection.execute(
                "DELETE FROM runs WHERE finished_at IS NOT NULL AND finished_at < ?",
                (run_cutoff,),
            ).rowcount
            digests = connection.execute(
                "DELETE FROM daily_digests WHERE emitted_at < ?", (closed_cutoff,)
            ).rowcount
        return {
            "candidates": candidates,
            "runs": runs,
            "digests": digests,
        }

    def expired_artifacts(self, *, now: datetime | None = None) -> tuple[tuple[str, Path], ...]:
        reference = now or datetime.now(UTC)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT artifact_id, path FROM artifacts WHERE expires_at < ? ORDER BY expires_at",
                (_utc_iso(reference),),
            ).fetchall()
        return tuple((str(row["artifact_id"]), Path(str(row["path"]))) for row in rows)

    def delete_artifact_record(self, lease: Lease, artifact_id: str) -> None:
        with self._transaction() as connection:
            self._assert_lease_row(connection, lease)
            connection.execute("DELETE FROM artifacts WHERE artifact_id = ?", (artifact_id,))

    def _assert_lease_row(self, connection: sqlite3.Connection, lease: Lease) -> None:
        row = connection.execute(
            "SELECT 1 FROM leases WHERE resource = ? AND run_id = ? AND owner_id = ? "
            "AND fencing_token = ? AND expires_at > ?",
            (
                lease.resource,
                lease.run_id,
                lease.owner_id,
                lease.fencing_token,
                _timestamp(None),
            ),
        ).fetchone()
        if row is None:
            raise LeaseLostError(f"lease lost for {lease.run_id}")

    def _validate_path(self) -> None:
        if self.path.exists() and self.path.is_symlink():
            raise ValueError("Loop state database must not be a symlink")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._validate_path()
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()


def _set_setting(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def _migrate_schema(connection: sqlite3.Connection) -> None:
    migrations = {
        "runs": {
            "mode": "TEXT NOT NULL DEFAULT 'DRY_RUN'",
            "target_branch": "TEXT",
        },
        "candidates": {
            "mode": "TEXT NOT NULL DEFAULT 'DRY_RUN'",
            "target_branch": "TEXT",
            "base_sha": "TEXT",
            "lifecycle_state": "TEXT NOT NULL DEFAULT 'DISCOVERED'",
            "suggested_tier": "TEXT",
            "changed_files_json": "TEXT NOT NULL DEFAULT '[]'",
            "tests_json": "TEXT NOT NULL DEFAULT '[]'",
            "risk_reasons_json": "TEXT NOT NULL DEFAULT '[]'",
            "confidence": "REAL",
            "diff_json": "TEXT",
        },
        "pull_requests": {
            "run_id": "TEXT",
            "branch": "TEXT",
            "url": "TEXT",
            "base_sha": "TEXT",
            "review_verdict": "TEXT",
            "review_summary": "TEXT",
            "created_day": "TEXT",
            "created_at": "TEXT",
        },
    }
    for table, columns in migrations.items():
        existing = {
            str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, declaration in columns.items():
            if name not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS pull_requests_branch_idx "
        "ON pull_requests(branch) WHERE branch IS NOT NULL"
    )


def _get_setting(connection: sqlite3.Connection, key: str, default: str) -> str:
    row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row is not None else default


def _register_outcome_row(
    connection: sqlite3.Connection, error_code: str | None
) -> tuple[int, bool]:
    if error_code is None:
        _set_setting(connection, "consecutive_error_code", "")
        _set_setting(connection, "consecutive_error_count", "0")
        return 0, False
    previous_code = _get_setting(connection, "consecutive_error_code", "")
    previous_count = int(_get_setting(connection, "consecutive_error_count", "0"))
    count = previous_count + 1 if previous_code == error_code else 1
    _set_setting(connection, "consecutive_error_code", error_code)
    _set_setting(connection, "consecutive_error_count", str(count))
    paused = count >= 3
    if paused:
        _set_setting(connection, "enabled", "0")
        _set_setting(connection, "mode", "PAUSED_ERROR")
    return count, paused


def _timestamp(value: datetime | None) -> float:
    return (value or datetime.now(UTC)).astimezone(UTC).timestamp()


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("digest boundaries must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _bounded(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized[:limit]


def _fingerprint(
    summary: str,
    evidence: tuple[str, ...],
    *,
    changed_files: tuple[str, ...] = (),
    target_branch: str | None = None,
) -> str:
    payload = json.dumps(
        {
            "summary": _bounded(summary, 500).casefold(),
            "evidence": evidence,
            "changed_files": changed_files,
            "target_branch": target_branch,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
