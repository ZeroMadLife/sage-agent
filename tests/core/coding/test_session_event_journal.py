from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest

from core.coding.persistence import session_event_journal as journal_module
from core.coding.persistence.session_event_journal import (
    SessionEventJournal,
    SessionEventJournalCorruptionError,
    SessionEventJournalError,
    SessionEventJournalOperationalError,
    SessionRunLeaseConflictError,
)


def _append(journal: SessionEventJournal, run_id: str = "run-1", **overrides: object):
    values = {
        "run_id": run_id,
        "kind": "tool",
        "status": "running",
        "payload": {"tool": "read_file"},
    }
    values.update(overrides)
    return journal.append(**values)


def test_replay_survives_restart_with_stable_ids_and_sequences(tmp_path: Path) -> None:
    first = SessionEventJournal(tmp_path, "session-1")
    stored = [_append(first), _append(first, kind="assistant", status="completed")]

    reopened = SessionEventJournal(tmp_path, "session-1")
    page = reopened.replay(after=0, limit=50)

    assert page.items == tuple(stored)
    assert [item.sequence for item in page.items] == [1, 2]
    assert page.next_cursor == 2
    assert page.has_more is False


def test_resume_run_reacquires_lease_without_duplicate_start(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    begun = journal.begin_run("run-1", owner_id="old-owner", owner_pid=os.getpid())
    journal.append(
        run_id="run-1",
        kind="user",
        status="completed",
        payload={"type": "user", "content": "continue"},
        lease_owner_id="old-owner",
        fencing_token=begun.fencing_token,
    )
    assert journal.release_run_lease(
        "run-1",
        owner_id="old-owner",
        fencing_token=begun.fencing_token,
    )

    token = journal.resume_run(
        "run-1",
        owner_id="new-owner",
        owner_pid=os.getpid(),
    )

    events = journal.replay(after=0, limit=20).items
    assert token > begun.fencing_token
    assert journal.active_run_id() == "run-1"
    assert [event.payload.get("event") for event in events].count("run_started") == 1
    assert journal.release_run_lease(
        "run-1",
        owner_id="new-owner",
        fencing_token=token,
    )


def test_recoverable_approval_only_returns_checkpoint_backed_interrupt(
    tmp_path: Path,
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    begun = journal.begin_run(
        "run-1",
        owner_id="owner-1",
        owner_pid=os.getpid(),
        surface_context={"surface": "knowledge"},
    )
    journal.append(
        run_id="run-1",
        kind="user",
        status="completed",
        payload={"type": "user", "content": "write note"},
        lease_owner_id="owner-1",
        fencing_token=begun.fencing_token,
    )
    journal.append(
        run_id="run-1",
        kind="approval",
        status="blocked",
        payload={
            "type": "approval_required",
            "runtime_profile": "deerflow_v2",
            "approval_id": "appr-1",
            "tool": "write_file",
            "args": {"path": "note.txt"},
            "description": "write_file requires approval.",
            "pattern_key": "tool:write_file",
            "tool_call_id": "call-1",
            "args_digest": "a" * 64,
        },
        lease_owner_id="owner-1",
        fencing_token=begun.fencing_token,
    )

    approval = journal.recoverable_approval("run-1")

    assert approval is not None
    assert approval["approval_id"] == "appr-1"
    assert approval["run_id"] == "run-1"
    assert approval["resume_attempt"] == 1
    assert journal.run_resume_context("run-1") == (
        "write note",
        {"surface": "knowledge"},
    )

    journal.append(
        run_id="run-1",
        kind="tool",
        status="completed",
        payload={"type": "tool_result", "tool_call_id": "call-1"},
        lease_owner_id="owner-1",
        fencing_token=begun.fencing_token,
    )
    assert journal.recoverable_approval("run-1") is None


def test_legacy_approval_is_not_treated_as_checkpoint_recoverable(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    journal.append(
        run_id="run-legacy",
        kind="approval",
        status="blocked",
        payload={
            "type": "approval_required",
            "approval_id": "legacy-approval",
            "tool": "run_shell",
        },
    )

    assert journal.recoverable_approval("run-legacy") is None


def test_terminal_run_never_exposes_stale_recoverable_approval(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    journal.append(
        run_id="run-terminal",
        kind="approval",
        status="blocked",
        payload={
            "type": "approval_required",
            "runtime_profile": "deerflow_v2",
            "approval_id": "appr-terminal",
            "tool": "write_file",
            "tool_call_id": "call-terminal",
            "args_digest": "b" * 64,
        },
    )
    journal.append_terminal_once(
        run_id="run-terminal",
        status="cancelled",
        payload={"event": "run_cancelled"},
    )

    assert journal.recoverable_approval("run-terminal") is None
    assert journal.recoverable_approval() is None


def test_replay_paginates_at_boundaries(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    for index in range(5):
        _append(journal, payload={"index": index})

    first = journal.replay(after=0, limit=2)
    second = journal.replay(after=first.next_cursor, limit=2)
    last = journal.replay(after=second.next_cursor, limit=2)

    assert [item.sequence for item in first.items] == [1, 2]
    assert first.has_more is True
    assert [item.sequence for item in second.items] == [3, 4]
    assert second.has_more is True
    assert [item.sequence for item in last.items] == [5]
    assert last.next_cursor == 5
    assert last.has_more is False
    with pytest.raises(ValueError, match="limit"):
        journal.replay(after=0, limit=0)
    with pytest.raises(ValueError, match="limit"):
        journal.replay(after=0, limit=501)
    with pytest.raises(ValueError, match="after"):
        journal.replay(after=-1, limit=1)


def test_active_subagent_run_ids_projects_unfinished_parent_children(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    journal.append(
        run_id="parent-1",
        kind="agent",
        status="running",
        payload={"type": "subagent_started", "child_run_id": "child-1"},
    )
    journal.append(
        run_id="parent-1",
        kind="agent",
        status="running",
        payload={"type": "subagent_started", "child_run_id": "child-2"},
    )
    journal.append(
        run_id="parent-1",
        kind="agent",
        status="completed",
        payload={"type": "subagent_completed", "child_run_id": "child-1"},
    )
    journal.append(
        run_id="other-parent",
        kind="agent",
        status="running",
        payload={"type": "subagent_started", "child_run_id": "child-other"},
    )

    assert journal.active_subagent_run_ids("parent-1") == ("child-2",)


def test_replay_before_reads_a_bounded_tail_and_older_pages(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    for index in range(250):
        _append(journal, payload={"index": index})

    tail = journal.replay_before(limit=100)
    older = journal.replay_before(before=tail.older_cursor, limit=100)
    oldest = journal.replay_before(before=older.older_cursor, limit=100)

    assert [item.sequence for item in tail.items] == list(range(151, 251))
    assert tail.older_cursor == 151
    assert tail.latest_cursor == 250
    assert tail.has_more is True
    assert [item.sequence for item in older.items] == list(range(51, 151))
    assert older.older_cursor == 51
    assert older.latest_cursor == 250
    assert older.has_more is True
    assert [item.sequence for item in oldest.items] == list(range(1, 51))
    assert oldest.older_cursor is None
    assert oldest.latest_cursor == 250
    assert oldest.has_more is False


def test_replay_before_validates_cursor_and_empty_tail(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")

    empty = journal.replay_before(limit=10)

    assert empty.items == ()
    assert empty.older_cursor is None
    assert empty.latest_cursor == 0
    with pytest.raises(ValueError, match="before"):
        journal.replay_before(before=0, limit=10)


def test_replay_before_uses_one_read_snapshot_for_rows_and_high_water(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    writer = SessionEventJournal(tmp_path, "session-1")
    _append(journal, payload={"index": 1})
    original_connect = journal._connect

    class InterleavingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection
            self.inserted = False

        def execute(self, sql: str, parameters=()):
            result = self.connection.execute(sql, parameters)
            if "MAX(sequence)" in sql and not self.inserted:
                self.inserted = True
                _append(writer, payload={"index": 2})
            return result

    @contextmanager
    def interleaving_connect():
        with original_connect() as connection:
            yield InterleavingConnection(connection)

    monkeypatch.setattr(journal, "_connect", interleaving_connect)

    page = journal.replay_before(limit=10)

    assert [item.sequence for item in page.items] == [1]
    assert page.latest_cursor == 1


def test_concurrent_append_allocates_strict_session_sequence(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    with ThreadPoolExecutor(max_workers=8) as executor:
        stored = list(executor.map(lambda index: _append(journal, payload={"index": index}), range(40)))

    assert sorted(item.sequence for item in stored) == list(range(1, 41))
    assert [item.sequence for item in journal.replay(after=0, limit=50).items] == list(range(1, 41))
    assert len({item.event_id for item in stored}) == 40


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "../escape"),
        ("kind", "unknown-kind"),
        ("status", "unknown-status"),
        ("payload", ["not", "an", "object"]),
        ("payload", {"nan": float("nan")}),
        ("payload", {"nested": {1: "non-string-key"}}),
    ],
)
def test_append_rejects_invalid_fields(tmp_path: Path, field: str, value: object) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    with pytest.raises((TypeError, ValueError), match=field):
        _append(journal, **{field: value})

    with pytest.raises(ValueError, match="session"):
        SessionEventJournal(tmp_path, "../escape")


@pytest.mark.parametrize(("field", "value"), [("event_id", ""), ("timestamp", "")])
def test_explicit_empty_generated_fields_are_rejected(
    tmp_path: Path, field: str, value: str
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    with pytest.raises(ValueError, match=field):
        _append(journal, **{field: value})


def test_schema_tampering_fails_closed_without_rebuild(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    _append(journal)
    with sqlite3.connect(journal.path) as connection:
        connection.execute("ALTER TABLE session_events ADD COLUMN injected TEXT")

    with pytest.raises(SessionEventJournalError, match="schema"):
        SessionEventJournal(tmp_path, "session-1")
    with sqlite3.connect(journal.path) as connection:
        assert "injected" in {row[1] for row in connection.execute("PRAGMA table_info(session_events)")}


def test_noncanonical_table_with_same_column_names_is_rejected(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    with sqlite3.connect(journal.path) as connection:
        connection.execute("DROP INDEX session_events_run_idx")
        connection.execute("DROP INDEX session_events_terminal_idx")
        connection.execute("DROP TABLE session_events")
        connection.execute(
            "CREATE TABLE session_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
            "event_id TEXT, session_id TEXT, run_id TEXT, kind TEXT, status TEXT, "
            "timestamp TEXT, payload_json TEXT)"
        )
        connection.execute(
            "CREATE INDEX session_events_run_idx ON session_events(run_id, sequence)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX session_events_terminal_idx ON session_events(run_id) "
            "WHERE kind = 'terminal'"
        )

    with pytest.raises(SessionEventJournalError, match="schema"):
        SessionEventJournal(tmp_path, "session-1")


def test_symlinked_session_or_database_is_rejected(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (evidence / "linked-session").symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, SessionEventJournalCorruptionError), match="symlink|unsafe"):
        SessionEventJournal(tmp_path, "linked-session")

    session = evidence / "session-1"
    session.mkdir()
    victim = tmp_path / "victim.sqlite3"
    victim.write_bytes(b"unchanged")
    (session / "timeline.sqlite3").symlink_to(victim)
    with pytest.raises((ValueError, SessionEventJournalCorruptionError), match="symlink|unsafe"):
        SessionEventJournal(tmp_path, "session-1")
    assert victim.read_bytes() == b"unchanged"


def test_corrupt_json_payload_is_detected_on_replay(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    event = _append(journal)
    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            "UPDATE session_events SET payload_json = ? WHERE event_id = ?",
            (json.dumps(["not-object"]), event.event_id),
        )

    with pytest.raises(SessionEventJournalCorruptionError, match="payload"):
        journal.replay(after=0, limit=10)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("timestamp", "2026-07-12T10:00:00"),
        ("status", "interrupted"),
    ],
)
def test_replay_rejects_tampered_nonterminal_rows(
    tmp_path: Path, column: str, value: str
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    event = _append(journal)
    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            f"UPDATE session_events SET {column} = ? WHERE event_id = ?",
            (value, event.event_id),
        )

    with pytest.raises(SessionEventJournalCorruptionError, match="timestamp|kind|status"):
        journal.replay(after=0, limit=10)


def test_replay_rejects_tampered_terminal_status_pair(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    event = journal.append_terminal_once(run_id="run-1", status="completed", payload={})
    with sqlite3.connect(journal.path) as connection:
        connection.execute(
            "UPDATE session_events SET status = 'running' WHERE event_id = ?",
            (event.event_id,),
        )

    with pytest.raises(SessionEventJournalCorruptionError, match="kind|status"):
        journal.replay(after=0, limit=10)


def test_terminal_append_is_atomic_and_idempotent(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    first = journal.append_terminal_once(
        run_id="run-1", status="completed", payload={"answer": "done"}
    )
    second = journal.append_terminal_once(
        run_id="run-1", status="error", payload={"error": "late"}
    )

    assert second == first
    assert [item.status for item in journal.replay(after=0, limit=10).items] == ["completed"]


def test_unlinked_open_wal_inode_is_a_normal_sqlite_lifecycle(tmp_path: Path) -> None:
    path = tmp_path / "timeline.sqlite3-wal"
    path.write_bytes(b"")
    file_fd = os.open(path, os.O_RDWR)
    path.unlink()
    try:
        journal_module._validate_optional_file(file_fd, path.name)
    finally:
        os.close(file_fd)


def test_run_lease_is_persistent_atomic_and_terminal_releases_it(tmp_path: Path) -> None:
    first = SessionEventJournal(tmp_path, "session-1")
    begun = first.begin_run("run-1", owner_id="owner-1")

    reopened = SessionEventJournal(tmp_path, "session-1")
    assert reopened.active_run_id() == "run-1"
    with pytest.raises(SessionRunLeaseConflictError, match="run-1"):
        reopened.acquire_run_lease("run-2")

    terminal = reopened.append_terminal_and_release(
        run_id="run-1",
        status="completed",
        payload={"answer": "done"},
        lease_owner_id="owner-1",
        fencing_token=begun.fencing_token,
    )
    assert terminal.kind == "terminal"
    assert reopened.active_run_id() is None


def test_active_lease_cannot_be_bypassed_by_omitting_fencing_credentials(
    tmp_path: Path,
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    journal.begin_run("run-1", owner_id="owner-1")

    with pytest.raises(SessionEventJournalError, match="lease|fenc|owner"):
        journal.append(run_id="run-1", kind="tool", status="done", payload={})
    with pytest.raises(SessionEventJournalError, match="lease|fenc|owner"):
        journal.append_terminal_and_release(
            run_id="run-1", status="completed", payload={}
        )
    with pytest.raises(SessionEventJournalError, match="lease|fenc|owner"):
        journal.append_terminal_once(
            run_id="run-1", status="completed", payload={}
        )

    assert journal.active_run_id() == "run-1"


def test_release_requires_matching_owner_and_fencing_token(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    begun = journal.begin_run("run-1", owner_id="owner-1", owner_pid=os.getpid())

    with pytest.raises(SessionEventJournalError, match="lease|fenc|owner"):
        journal.release_run_lease(
            "run-1", owner_id="wrong-owner", fencing_token=begun.fencing_token
        )
    assert journal.active_run_id() == "run-1"
    assert journal.release_run_lease(
        "run-1", owner_id="owner-1", fencing_token=begun.fencing_token
    ) is True


def test_pid_reuse_does_not_make_previous_process_owner_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process_starts = iter(
        (
            journal_module._ProcessIdentity(
                journal_module._ProcessIdentityState.PRESENT, "linux:boot:old-start"
            ),
            journal_module._ProcessIdentity(
                journal_module._ProcessIdentityState.PRESENT, "linux:boot:new-start"
            ),
        )
    )
    monkeypatch.setattr(
        journal_module,
        "_process_identity",
        lambda _pid: next(process_starts),
    )
    journal = SessionEventJournal(tmp_path, "session-1")
    journal.begin_run("run-1", owner_id="previous-process", owner_pid=os.getpid())

    recovered = journal.recover_run_lease(recovery_owner_id="current-process")

    assert recovered is not None
    assert recovered.status == "interrupted"
    assert journal.active_run_id() is None


def test_transient_identity_failure_does_not_recover_live_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    journal.begin_run("run-1", owner_id="owner-1", owner_pid=os.getpid())
    monkeypatch.setattr(
        journal_module,
        "_process_identity",
        lambda _pid: journal_module._ProcessIdentity(
            journal_module._ProcessIdentityState.UNKNOWN
        ),
    )

    with pytest.raises(SessionEventJournalOperationalError, match="temporarily unavailable"):
        journal.recover_run_lease()

    assert journal.active_run_id() == "run-1"


def test_begin_run_fails_when_live_owner_identity_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    monkeypatch.setattr(
        journal_module,
        "_process_identity",
        lambda _pid: journal_module._ProcessIdentity(
            journal_module._ProcessIdentityState.UNKNOWN
        ),
    )

    with pytest.raises(SessionEventJournalOperationalError, match="reliable process identity"):
        journal.begin_run("run-1", owner_id="owner-1", owner_pid=os.getpid())

    assert journal.active_run_id() is None
    assert journal.replay(after=0, limit=10).items == ()


def test_recover_run_lease_marks_interrupted_and_releases_atomically(tmp_path: Path) -> None:
    first = SessionEventJournal(tmp_path, "session-1")
    first.acquire_run_lease("abandoned")

    reopened = SessionEventJournal(tmp_path, "session-1")
    recovered = reopened.recover_run_lease()

    assert recovered is not None
    assert recovered.run_id == "abandoned"
    assert recovered.status == "interrupted"
    assert recovered.payload == {"event": "run_interrupted", "retryable": True}
    assert reopened.active_run_id() is None
    assert reopened.recover_run_lease() is None


def test_v1_journal_migrates_to_persistent_lease_schema(tmp_path: Path) -> None:
    root = tmp_path / "evidence" / "session-1"
    root.mkdir(parents=True)
    path = root / "timeline.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(journal_module._EVENTS_SQL)
        connection.execute(journal_module._RUN_INDEX_SQL)
        connection.execute(journal_module._TERMINAL_INDEX_SQL)
        connection.execute("PRAGMA user_version=1")
        connection.execute(
            "INSERT INTO session_events "
            "(event_id, session_id, run_id, kind, status, timestamp, payload_json) "
            "VALUES ('event-1', 'session-1', 'run-1', 'user', 'completed', "
            "'2026-07-12T10:00:00+08:00', '{}')"
        )

    migrated = SessionEventJournal(tmp_path, "session-1")

    assert [item.event_id for item in migrated.replay(after=0, limit=10).items] == ["event-1"]
    assert migrated.active_run_id() is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == journal_module.SCHEMA_VERSION
        assert connection.execute(
            "SELECT name FROM sqlite_schema WHERE name = 'active_run_lease'"
        ).fetchone() == ("active_run_lease",)


def test_v2_migration_preserves_active_lease_as_legacy_owner(tmp_path: Path) -> None:
    root = tmp_path / "evidence" / "session-1"
    root.mkdir(parents=True)
    path = root / "timeline.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(journal_module._EVENTS_SQL)
        connection.execute(journal_module._RUN_INDEX_SQL)
        connection.execute(journal_module._TERMINAL_INDEX_SQL)
        connection.execute(journal_module._LEASE_V2_SQL)
        connection.execute(
            "INSERT INTO active_run_lease (lease_key, run_id, acquired_at) "
            "VALUES (1, 'run-1', '2026-07-12T10:00:00+08:00')"
        )
        connection.execute("PRAGMA user_version=2")

    migrated = SessionEventJournal(tmp_path, "session-1")

    assert migrated.active_run_id() == "run-1"
    recovered = migrated.recover_run_lease(recovery_owner_id="new-process")
    assert recovered is not None
    assert recovered.status == "interrupted"
    assert migrated.active_run_id() is None


def test_locked_database_is_transient_not_corruption(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1", busy_timeout_seconds=0.01)
    blocker = sqlite3.connect(journal.path)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(SessionEventJournalOperationalError, match="busy|locked"):
            _append(journal)
    finally:
        blocker.rollback()
        blocker.close()


def test_analyze_internal_schema_reopens_without_weakening_app_schema(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    _append(journal)
    with sqlite3.connect(journal.path) as connection:
        connection.execute("ANALYZE")

    reopened = SessionEventJournal(tmp_path, "session-1")
    assert len(reopened.replay(after=0, limit=10).items) == 1


def test_connect_rejects_group_writable_session_directory(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    journal.root.chmod(0o770)
    try:
        with pytest.raises(SessionEventJournalCorruptionError, match="unsafe"):
            journal.replay(after=0, limit=10)
    finally:
        journal.root.chmod(0o700)


def test_reopen_does_not_silently_repair_unsafe_session_permissions(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    journal.root.chmod(0o770)
    try:
        with pytest.raises((ValueError, SessionEventJournalCorruptionError), match="unsafe"):
            SessionEventJournal(tmp_path, "session-1")
    finally:
        journal.root.chmod(0o700)


def test_reopen_does_not_silently_repair_unsafe_storage_root(tmp_path: Path) -> None:
    SessionEventJournal(tmp_path, "session-1")
    original_mode = tmp_path.stat().st_mode & 0o777
    tmp_path.chmod(0o770)
    try:
        with pytest.raises(ValueError, match="unsafe"):
            SessionEventJournal(tmp_path, "session-1")
    finally:
        tmp_path.chmod(original_mode)
