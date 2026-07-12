from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from core.coding.persistence import session_event_journal as journal_module
from core.coding.persistence.session_event_journal import (
    SessionEventJournal,
    SessionEventJournalCorruptionError,
    SessionEventJournalError,
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
