from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from core.loop_harness.errors import LeaseBusyError, LeaseLostError
from core.loop_harness.models import PullRequestReceipt, ReviewerResult, WorkerResult
from core.loop_harness.state import LoopState


def test_lease_recovery_allocates_new_fencing_token(tmp_path) -> None:
    state = LoopState(tmp_path / "state.sqlite3")
    state.initialize()
    now = datetime(2026, 7, 16, 0, 0, tzinfo=UTC)
    first = state.acquire_lease(
        resource="loop-run",
        run_id="run-1",
        owner_id="owner-1",
        ttl_seconds=10,
        now=now,
    )

    with pytest.raises(LeaseBusyError):
        state.acquire_lease(
            resource="loop-run",
            run_id="run-2",
            owner_id="owner-2",
            ttl_seconds=10,
            now=now + timedelta(seconds=5),
        )

    second = state.acquire_lease(
        resource="loop-run",
        run_id="run-2",
        owner_id="owner-2",
        ttl_seconds=10,
        now=now + timedelta(seconds=11),
    )

    assert second.fencing_token == first.fencing_token + 1
    with pytest.raises(LeaseLostError):
        state.assert_lease(first, now=now + timedelta(seconds=11))
    state.assert_lease(second, now=now + timedelta(seconds=11))


def test_terminalize_updates_run_and_releases_lease_atomically(tmp_path) -> None:
    state = LoopState(tmp_path / "state.sqlite3")
    state.initialize()
    lease = state.acquire_lease(
        resource="loop-run",
        run_id="run-1",
        owner_id="owner-1",
        ttl_seconds=60,
    )
    state.begin_run(lease, policy_version="1.0")
    state.set_run_base_sha(lease, "a" * 40)

    state.terminalize(lease, state="NO_OP", summary="nothing found")

    status = state.status()
    assert status["lease"] is None
    assert status["latest_run"]["state"] == "NO_OP"
    with pytest.raises(LeaseLostError):
        state.assert_lease(lease)


def test_same_infrastructure_error_pauses_after_three_runs(tmp_path) -> None:
    state = LoopState(tmp_path / "state.sqlite3")
    state.initialize()
    state.set_enabled(True)

    outcomes = []
    for index in range(1, 4):
        lease = state.acquire_lease(
            resource="loop-run",
            run_id=f"run-{index}",
            owner_id=f"owner-{index}",
            ttl_seconds=60,
        )
        state.begin_run(lease, policy_version="1.0")
        outcomes.append(
            state.terminalize(
                lease,
                state="BLOCKED",
                summary="human work is present",
                error_code="BLOCKED_ROOT_DIRTY",
            )
        )

    assert outcomes == [(1, False), (2, False), (3, True)]

    assert state.is_enabled() is False
    assert state.status()["mode"] == "PAUSED_ERROR"


def test_stale_fencing_token_cannot_record_candidate(tmp_path) -> None:
    state = LoopState(tmp_path / "state.sqlite3")
    state.initialize()
    first = state.acquire_lease(
        resource="loop-run",
        run_id="run-old",
        owner_id="owner-old",
        ttl_seconds=60,
    )
    state.begin_run(first, policy_version="1.0")
    state.acquire_lease(
        resource="loop-run",
        run_id="run-new",
        owner_id="owner-new",
        ttl_seconds=60,
        now=datetime.now(UTC) + timedelta(seconds=61),
    )
    result = WorkerResult(
        verdict="REPORT",
        summary="stale result",
        evidence=("old evidence",),
        reproduction=(),
        changed_files=(),
        tests=(),
        risk_reasons=(),
        suggested_tier="C",
        confidence=0.9,
    )

    with pytest.raises(LeaseLostError):
        state.record_worker_result(first, result)

    with sqlite3.connect(state.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0


def test_candidate_and_daily_digest_are_deduplicated(tmp_path) -> None:
    state = LoopState(tmp_path / "state.sqlite3")
    state.initialize()
    result = WorkerResult(
        verdict="REPORT",
        summary="shared schema change is required",
        evidence=("api/schema.py:10",),
        reproduction=("run test",),
        changed_files=(),
        tests=(),
        risk_reasons=("shared contract",),
        suggested_tier="C",
        confidence=0.9,
    )

    lease = state.acquire_lease(
        resource="loop-run",
        run_id="run-report",
        owner_id="owner-report",
        ttl_seconds=60,
    )
    state.begin_run(lease, policy_version="1.0")
    finding_1 = state.record_worker_result(lease, result)
    finding_2 = state.record_worker_result(lease, result)

    assert finding_1 == finding_2
    with sqlite3.connect(state.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
        assert connection.execute("SELECT occurrence_count FROM candidates").fetchone()[0] == 2

    start = datetime(2026, 7, 16, tzinfo=UTC)
    first = state.digest(
        digest_date="2026-07-16",
        start_at=start,
        end_at=start + timedelta(days=1),
    )
    second = state.digest(
        digest_date="2026-07-16",
        start_at=start,
        end_at=start + timedelta(days=1),
    )
    assert first is not None
    assert second is None


def test_scan_scope_rotates_by_oldest_cursor(tmp_path) -> None:
    state = LoopState(tmp_path / "state.sqlite3")
    state.initialize()
    scopes = (("core", "tests/core"), ("api", "tests/api"))

    first = state.choose_scan_scope(scopes)
    lease = state.acquire_lease(
        resource="loop-run",
        run_id="run-scan",
        owner_id="owner-scan",
        ttl_seconds=60,
    )
    state.begin_run(lease, policy_version="1.0")
    state.record_scan_scope(lease, first, "a" * 40)
    second = state.choose_scan_scope(scopes)

    assert first == scopes[0]
    assert second == scopes[1]


def test_state_database_rejects_symlink(tmp_path) -> None:
    target = tmp_path / "target.sqlite3"
    target.touch()
    link = tmp_path / "state.sqlite3"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="must not be a symlink"):
        LoopState(link)


def test_shadow_write_mode_is_explicit_and_invalid_modes_fail_closed(tmp_path) -> None:
    state = LoopState(tmp_path / "state.sqlite3")
    state.initialize()

    state.set_enabled(True, mode="SHADOW_WRITE")

    assert state.status()["enabled"] is True
    assert state.status()["mode"] == "SHADOW_WRITE"
    state.set_enabled(True, mode="PR_CANARY")
    assert state.status()["mode"] == "PR_CANARY"
    with pytest.raises(ValueError, match="unsupported Loop mode"):
        state.set_enabled(True, mode="AUTO_MERGE")
    assert state.status()["mode"] == "PR_CANARY"


def test_pr_capacity_and_review_are_bound_to_exact_head(tmp_path) -> None:
    state = LoopState(tmp_path / "state.sqlite3")
    state.initialize()
    state.set_enabled(True, mode="PR_CANARY")
    lease = state.acquire_lease(
        resource="loop-run",
        run_id="run-pr",
        owner_id="owner-pr",
        ttl_seconds=60,
    )
    state.begin_run(lease, policy_version="2.1", mode="PR_CANARY")
    created_day = state.require_pr_capacity(
        lease, now=datetime(2026, 7, 16, 12, tzinfo=UTC)
    )
    receipt = PullRequestReceipt(
        12,
        "https://github.com/ZeroMadLife/sage-agent/pull/12",
        "codex/loop-frontend-abcdef123456",
        "b" * 40,
    )
    state.record_pull_request(
        lease,
        receipt,
        base_sha="a" * 40,
        tier="A",
        created_day=created_day,
    )

    with pytest.raises(LeaseBusyError):
        state.require_pr_capacity(lease, now=datetime(2026, 7, 16, 13, tzinfo=UTC))

    review = ReviewerResult(
        "PASS",
        "未发现阻断问题",
        (),
        "测试通过",
        "不要求截图",
        "边界通过",
        "保持 Draft",
    )
    state.record_review_result(
        lease,
        pr_number=12,
        head_sha="b" * 40,
        result=review,
    )

    with sqlite3.connect(state.path) as connection:
        row = connection.execute(
            "SELECT state, review_verdict, created_day FROM pull_requests WHERE number = 12"
        ).fetchone()
    assert row == ("DRAFT_REVIEWED", "PASS", "2026-07-16")
    assert state.status()["open_pull_requests"] == 1


def test_initialize_migrates_existing_phase1_database(tmp_path) -> None:
    database = tmp_path / "state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                base_sha TEXT,
                policy_version TEXT NOT NULL,
                state TEXT NOT NULL,
                error_code TEXT,
                summary TEXT NOT NULL DEFAULT '',
                started_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE TABLE candidates (
                fingerprint TEXT PRIMARY KEY,
                verdict TEXT NOT NULL,
                summary TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                occurrence_count INTEGER NOT NULL,
                cooldown_until TEXT
            );
            """
        )

    state = LoopState(database)
    state.initialize()

    with sqlite3.connect(database) as connection:
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
        candidate_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(candidates)")
        }
    assert {"mode", "target_branch"} <= run_columns
    assert {"lifecycle_state", "changed_files_json", "diff_json"} <= candidate_columns
