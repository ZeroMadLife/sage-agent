"""Durable, session-scoped browser event journal.

Paths are fail-closed against accidental links, ownership changes, and writable
service directories. Same-UID malicious concurrent replacement is outside the
stdlib sqlite3 threat boundary and requires a process sandbox or fd-backed VFS.
"""

from __future__ import annotations

import errno
import json
import math
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 4
_DATABASE_NAME = "timeline.sqlite3"
_MAX_PAYLOAD_BYTES = 1024 * 1024
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_KINDS = {
    "user", "assistant", "model", "tool", "approval", "context", "memory",
    "agent", "terminal", "system", "run",
}
_STATUSES = {
    "pending", "queued", "running", "blocked", "done", "completed", "cancelled",
    "error", "interrupted", "retryable",
}
TERMINAL_STATUSES = frozenset({"completed", "cancelled", "error", "interrupted"})
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_CLOEXEC | os.O_NOFOLLOW
_SIDECAR_SUFFIXES = ("-wal", "-shm")

_EVENTS_SQL = """
CREATE TABLE session_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload_json TEXT NOT NULL
)
"""
_RUN_INDEX_SQL = """
CREATE INDEX session_events_run_idx ON session_events(run_id, sequence)
"""
_TERMINAL_INDEX_SQL = """
CREATE UNIQUE INDEX session_events_terminal_idx ON session_events(run_id)
WHERE kind = 'terminal'
"""
_LEASE_V2_SQL = """
CREATE TABLE active_run_lease (
    lease_key INTEGER PRIMARY KEY CHECK (lease_key = 1),
    run_id TEXT NOT NULL UNIQUE,
    acquired_at TEXT NOT NULL
)
"""
_LEASE_V3_SQL = """
CREATE TABLE active_run_lease (
    lease_key INTEGER PRIMARY KEY CHECK (lease_key = 1),
    run_id TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    acquired_at TEXT NOT NULL
)
"""
_LEASE_SQL = """
CREATE TABLE active_run_lease (
    lease_key INTEGER PRIMARY KEY CHECK (lease_key = 1),
    run_id TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    owner_pid INTEGER NOT NULL,
    fencing_token INTEGER NOT NULL,
    acquired_at TEXT NOT NULL
)
"""
_FENCE_SQL = """
CREATE TABLE run_fence_state (
    state_key INTEGER PRIMARY KEY CHECK (state_key = 1),
    next_token INTEGER NOT NULL CHECK (next_token > 0)
)
"""


class SessionEventJournalError(RuntimeError):
    """Base error for event journal persistence."""


class SessionEventJournalCorruptionError(SessionEventJournalError):
    """The journal path, database, schema, or stored data is unsafe."""


class SessionEventJournalOperationalError(SessionEventJournalError):
    """A transient SQLite operational condition such as BUSY or LOCKED."""


class SessionRunLeaseConflictError(SessionEventJournalError):
    """Another coordinator owns the session's persistent run lease."""

    def __init__(self, active_run_id: str) -> None:
        self.active_run_id = active_run_id
        super().__init__(f"session already has active run {active_run_id}")


class SessionRunLeaseLostError(SessionEventJournalError):
    """The run owner or fencing token no longer owns the active lease."""


@dataclass(frozen=True, slots=True)
class SessionEvent:
    event_id: str
    session_id: str
    run_id: str
    sequence: int
    kind: str
    status: str
    timestamp: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ReplayPage:
    items: tuple[SessionEvent, ...]
    next_cursor: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class BeginRunResult:
    event: SessionEvent
    fencing_token: int


class SessionEventJournal:
    """SQLite event journal bound to one server-owned session directory."""

    def __init__(
        self, storage_root: Path, session_id: str, *, busy_timeout_seconds: float = 5.0
    ) -> None:
        if busy_timeout_seconds < 0:
            raise ValueError("busy_timeout_seconds must be non-negative")
        self._busy_timeout_seconds = busy_timeout_seconds
        self.session_id = _validate_identifier("session_id", session_id)
        self._root = _trusted_root(storage_root)
        self._components = ("evidence", self.session_id)
        self.root = self._root.joinpath(*self._components)
        self.path = self.root / _DATABASE_NAME
        directory_fd = _open_directory(
            self._root, self._components, create=True, tighten=True
        )
        try:
            _prepare_database_file(directory_fd)
        finally:
            os.close(directory_fd)
        self._initialize()

    def append(
        self,
        *,
        run_id: str,
        kind: str,
        status: str,
        payload: Mapping[str, Any],
        event_id: str | None = None,
        timestamp: str | None = None,
        lease_owner_id: str | None = None,
        fencing_token: int | None = None,
    ) -> SessionEvent:
        """Commit an event and return only after it is durable."""
        values = _validated_event_input(
            run_id=run_id,
            kind=kind,
            status=status,
            payload=payload,
            event_id=event_id,
            timestamp=timestamp,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                _assert_run_lease(
                    connection,
                    run_id=run_id,
                    owner_id=lease_owner_id,
                    fencing_token=fencing_token,
                )
                stored = self._insert(connection, **values)
                connection.commit()
                return stored
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise SessionEventJournalError("event id or terminal event conflicts") from exc

    def append_terminal_once(
        self,
        *,
        run_id: str,
        status: str,
        payload: Mapping[str, Any],
        event_id: str | None = None,
        timestamp: str | None = None,
    ) -> SessionEvent:
        """Atomically append the first terminal event for a run."""
        if status not in TERMINAL_STATUSES:
            raise ValueError("status must be terminal")
        values = _validated_event_input(
            run_id=run_id,
            kind="terminal",
            status=status,
            payload=payload,
            event_id=event_id,
            timestamp=timestamp,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                stored = self._terminal_in_transaction(connection, values)
                connection.commit()
                return stored
            except Exception:
                connection.rollback()
                raise

    def append_terminal_and_release(
        self,
        *,
        run_id: str,
        status: str,
        payload: Mapping[str, Any],
        event_id: str | None = None,
        timestamp: str | None = None,
        lease_owner_id: str | None = None,
        fencing_token: int | None = None,
    ) -> SessionEvent:
        """Persist one terminal and release its run lease in one transaction."""
        if status not in TERMINAL_STATUSES:
            raise ValueError("status must be terminal")
        values = _validated_event_input(
            run_id=run_id,
            kind="terminal",
            status=status,
            payload=payload,
            event_id=event_id,
            timestamp=timestamp,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                _assert_run_lease(
                    connection,
                    run_id=run_id,
                    owner_id=lease_owner_id,
                    fencing_token=fencing_token,
                )
                stored = self._terminal_in_transaction(connection, values)
                connection.execute(
                    "DELETE FROM active_run_lease WHERE lease_key = 1 AND run_id = ?",
                    (values["run_id"],),
                )
                connection.commit()
                return stored
            except Exception:
                connection.rollback()
                raise

    def acquire_run_lease(
        self, run_id: str, *, owner_id: str = "legacy", owner_pid: int = -1
    ) -> None:
        """Atomically acquire this session's singleton persistent run lease."""
        validated_run = _validate_identifier("run_id", run_id)
        validated_owner = _validate_identifier("owner_id", owner_id)
        acquired_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                terminal = connection.execute(
                    "SELECT 1 FROM session_events WHERE run_id = ? AND kind = 'terminal'",
                    (validated_run,),
                ).fetchone()
                if terminal is not None:
                    raise SessionEventJournalError(f"run {validated_run} is already terminal")
                token = _allocate_fencing_token(connection)
                connection.execute(
                    "INSERT INTO active_run_lease "
                    "(lease_key, run_id, owner_id, owner_pid, fencing_token, acquired_at) "
                    "VALUES (1, ?, ?, ?, ?, ?)",
                    (validated_run, validated_owner, owner_pid, token, acquired_at),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                row = connection.execute(
                    "SELECT run_id FROM active_run_lease WHERE lease_key = 1"
                ).fetchone()
                active = str(row[0]) if row is not None else "unknown"
                raise SessionRunLeaseConflictError(active) from exc
            except Exception:
                connection.rollback()
                raise

    def begin_run(
        self, run_id: str, *, owner_id: str = "legacy", owner_pid: int = -1
    ) -> BeginRunResult:
        """Acquire the singleton lease and persist run_started atomically."""
        validated_run = _validate_identifier("run_id", run_id)
        validated_owner = _validate_identifier("owner_id", owner_id)
        acquired_at = datetime.now(UTC).isoformat()
        values = _validated_event_input(
            run_id=validated_run,
            kind="system",
            status="running",
            payload={"event": "run_started"},
            event_id=None,
            timestamp=acquired_at,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                lease = connection.execute(
                    "SELECT run_id FROM active_run_lease WHERE lease_key = 1"
                ).fetchone()
                if lease is not None:
                    raise SessionRunLeaseConflictError(str(lease[0]))
                terminal = connection.execute(
                    "SELECT 1 FROM session_events WHERE run_id = ? AND kind = 'terminal'",
                    (validated_run,),
                ).fetchone()
                if terminal is not None:
                    raise SessionEventJournalError(f"run {validated_run} is already terminal")
                token = _allocate_fencing_token(connection)
                connection.execute(
                    "INSERT INTO active_run_lease "
                    "(lease_key, run_id, owner_id, owner_pid, fencing_token, acquired_at) "
                    "VALUES (1, ?, ?, ?, ?, ?)",
                    (validated_run, validated_owner, owner_pid, token, acquired_at),
                )
                stored = self._insert(connection, **values)
                connection.commit()
                return BeginRunResult(event=stored, fencing_token=token)
            except Exception:
                connection.rollback()
                raise

    def release_run_lease(
        self, run_id: str, *, owner_id: str, fencing_token: int
    ) -> bool:
        """Release a matching lease, used when startup fails before task ownership."""
        validated_run = _validate_identifier("run_id", run_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _assert_run_lease(
                connection,
                run_id=validated_run,
                owner_id=owner_id,
                fencing_token=fencing_token,
            )
            cursor = connection.execute(
                "DELETE FROM active_run_lease WHERE lease_key = 1 AND run_id = ?",
                (validated_run,),
            )
            connection.commit()
            return cursor.rowcount == 1

    def active_run_id(self) -> str | None:
        """Return the run currently holding the persistent session lease."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT run_id FROM active_run_lease WHERE lease_key = 1"
            ).fetchone()
        return str(row[0]) if row is not None else None

    def recover_run_lease(
        self,
        *,
        recovery_owner_id: str = "recovery",
        live_owner_ids: Collection[str] = (),
    ) -> SessionEvent | None:
        """Atomically interrupt and release a lease abandoned by a prior process."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                owner = _validate_identifier("recovery_owner_id", recovery_owner_id)
                row = connection.execute(
                    "SELECT run_id, owner_id, owner_pid FROM active_run_lease WHERE lease_key = 1"
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                run_id = str(row[0])
                if str(row[1]) == owner:
                    connection.commit()
                    return None
                owner_pid = int(row[2])
                if _pid_is_alive(owner_pid) and (
                    owner_pid != os.getpid() or str(row[1]) in live_owner_ids
                ):
                    connection.commit()
                    return None
                existing = connection.execute(
                    "SELECT * FROM session_events WHERE run_id = ? AND kind = 'terminal'",
                    (run_id,),
                ).fetchone()
                if existing is None:
                    values = _validated_event_input(
                        run_id=run_id,
                        kind="terminal",
                        status="interrupted",
                        payload={"event": "run_interrupted", "retryable": True},
                        event_id=None,
                        timestamp=None,
                    )
                    stored = self._insert(connection, **values)
                else:
                    stored = None
                connection.execute("DELETE FROM active_run_lease WHERE lease_key = 1")
                connection.commit()
                return stored
            except Exception:
                connection.rollback()
                raise

    def replay(self, *, after: int = 0, limit: int = 100) -> ReplayPage:
        """Return events after a sequence cursor in ascending order."""
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ValueError("after must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM session_events WHERE sequence > ? ORDER BY sequence LIMIT ?",
                (after, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        items = tuple(_event_from_row(row, self.session_id) for row in rows[:limit])
        return ReplayPage(
            items=items,
            next_cursor=items[-1].sequence if items else after,
            has_more=has_more,
        )

    def unfinished_run_ids(self) -> tuple[str, ...]:
        """Return runs with a start event and no terminal event."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT run_id FROM session_events GROUP BY run_id "
                "HAVING SUM(CASE WHEN status = 'running' AND "
                "json_extract(payload_json, '$.event') = 'run_started' THEN 1 ELSE 0 END) > 0 "
                "AND SUM(CASE WHEN kind = 'terminal' "
                "THEN 1 ELSE 0 END) = 0 "
                "AND run_id NOT IN (SELECT run_id FROM active_run_lease) "
                "ORDER BY MIN(sequence)"
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

    def latest_sequence(self) -> int:
        """Return the durable high-water sequence for this session."""
        with self._connect() as connection:
            row = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM session_events").fetchone()
        return int(row[0])

    def _insert(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        kind: str,
        status: str,
        payload_json: str,
        event_id: str,
        timestamp: str,
    ) -> SessionEvent:
        cursor = connection.execute(
            "INSERT INTO session_events "
            "(event_id, session_id, run_id, kind, status, timestamp, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, self.session_id, run_id, kind, status, timestamp, payload_json),
        )
        row = connection.execute(
            "SELECT * FROM session_events WHERE sequence = ?", (cursor.lastrowid,)
        ).fetchone()
        if row is None:
            raise SessionEventJournalError("inserted event could not be read")
        return _event_from_row(row, self.session_id)

    def _terminal_in_transaction(
        self, connection: sqlite3.Connection, values: dict[str, str]
    ) -> SessionEvent:
        row = connection.execute(
            "SELECT * FROM session_events WHERE run_id = ? AND kind = 'terminal' "
            "ORDER BY sequence LIMIT 1",
            (values["run_id"],),
        ).fetchone()
        if row is not None:
            return _event_from_row(row, self.session_id)
        return self._insert(connection, **values)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                objects = _schema_objects(connection)
                if version == 0 and not objects:
                    connection.execute(_EVENTS_SQL)
                    connection.execute(_RUN_INDEX_SQL)
                    connection.execute(_TERMINAL_INDEX_SQL)
                    connection.execute(_LEASE_SQL)
                    connection.execute(_FENCE_SQL)
                    connection.execute(
                        "INSERT INTO run_fence_state (state_key, next_token) VALUES (1, 1)"
                    )
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                elif version == 1:
                    _validate_schema(connection, self.path, lease_version=0)
                    connection.execute(_LEASE_SQL)
                    connection.execute(_FENCE_SQL)
                    connection.execute(
                        "INSERT INTO run_fence_state (state_key, next_token) VALUES (1, 1)"
                    )
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                elif version == 2:
                    _validate_schema(connection, self.path, lease_version=2)
                    existing = connection.execute(
                        "SELECT run_id, acquired_at FROM active_run_lease WHERE lease_key = 1"
                    ).fetchone()
                    connection.execute("DROP TABLE active_run_lease")
                    connection.execute(_LEASE_SQL)
                    connection.execute(_FENCE_SQL)
                    next_token = 1
                    if existing is not None:
                        connection.execute(
                            "INSERT INTO active_run_lease "
                            "(lease_key, run_id, owner_id, owner_pid, fencing_token, acquired_at) "
                            "VALUES (1, ?, 'legacy', -1, 1, ?)",
                            (str(existing[0]), str(existing[1])),
                        )
                        next_token = 2
                    connection.execute(
                        "INSERT INTO run_fence_state (state_key, next_token) VALUES (1, ?)",
                        (next_token,),
                    )
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                elif version == 3:
                    _validate_schema(connection, self.path, lease_version=3)
                    existing = connection.execute(
                        "SELECT run_id, owner_id, fencing_token, acquired_at "
                        "FROM active_run_lease WHERE lease_key = 1"
                    ).fetchone()
                    connection.execute("DROP TABLE active_run_lease")
                    connection.execute(_LEASE_SQL)
                    if existing is not None:
                        connection.execute(
                            "INSERT INTO active_run_lease "
                            "(lease_key, run_id, owner_id, owner_pid, fencing_token, acquired_at) "
                            "VALUES (1, ?, ?, -1, ?, ?)",
                            tuple(existing),
                        )
                    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                elif version != SCHEMA_VERSION:
                    raise SessionEventJournalError(
                        f"unsupported session event schema version {version} at {self.path}"
                    )
                _validate_schema(connection, self.path)
                integrity = connection.execute("PRAGMA integrity_check").fetchall()
                if [tuple(row) for row in integrity] != [("ok",)]:
                    raise SessionEventJournalCorruptionError(
                        f"session event database failed integrity check at {self.path}"
                    )
                connection.commit()
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        expected = self._verify_database_and_sidecars()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.path, timeout=self._busy_timeout_seconds)
            self._verify_connected_inode(expected)
            connection.row_factory = sqlite3.Row
            connection.execute(f"PRAGMA busy_timeout={int(self._busy_timeout_seconds * 1000)}")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        except sqlite3.OperationalError as exc:
            if _is_busy_or_locked(exc):
                raise SessionEventJournalOperationalError(
                    f"session event database busy or locked at {self.path}"
                ) from exc
            raise SessionEventJournalError(
                f"session event database operational error at {self.path}"
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise SessionEventJournalCorruptionError(
                f"session event database error at {self.path}"
            ) from exc
        finally:
            try:
                if connection is not None:
                    connection.close()
            finally:
                self._verify_database_and_sidecars()

    def _verify_database_and_sidecars(self) -> tuple[int, int]:
        try:
            directory_fd = _open_directory(
                self._root, self._components, create=False, tighten=False
            )
            try:
                database_fd = _open_verified_file(directory_fd, _DATABASE_NAME)
                try:
                    metadata = os.fstat(database_fd)
                    os.fchmod(database_fd, 0o600)
                    expected = (metadata.st_dev, metadata.st_ino)
                finally:
                    os.close(database_fd)
                for suffix in _SIDECAR_SUFFIXES:
                    _secure_optional_file(directory_fd, _DATABASE_NAME + suffix)
                return expected
            finally:
                os.close(directory_fd)
        except (OSError, ValueError) as exc:
            raise SessionEventJournalCorruptionError(
                "session event database path is unsafe"
            ) from exc

    def _verify_connected_inode(self, expected: tuple[int, int]) -> None:
        if self._verify_database_and_sidecars() != expected:
            raise SessionEventJournalCorruptionError(
                "session event database changed while opening"
            )


def _validated_event_input(
    *,
    run_id: str,
    kind: str,
    status: str,
    payload: Mapping[str, Any],
    event_id: str | None,
    timestamp: str | None,
) -> dict[str, str]:
    validated_run = _validate_identifier("run_id", run_id)
    if kind not in _KINDS:
        raise ValueError(f"invalid kind: {kind!r}")
    if status not in _STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    _validate_kind_status(kind, status)
    if not isinstance(payload, dict):
        raise TypeError("payload must be a JSON object")
    if not _strict_json_value(payload):
        raise ValueError("payload must contain strict JSON values with string object keys")
    try:
        payload_json = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("payload must contain strict JSON values") from exc
    if len(payload_json.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("payload exceeds maximum size")
    actual_event_id = str(uuid.uuid4()) if event_id is None else event_id
    _validate_identifier("event_id", actual_event_id)
    actual_timestamp = datetime.now(UTC).isoformat() if timestamp is None else timestamp
    _validate_timestamp(actual_timestamp)
    return {
        "run_id": validated_run,
        "kind": kind,
        "status": status,
        "payload_json": payload_json,
        "event_id": actual_event_id,
        "timestamp": actual_timestamp,
    }


def _validate_timestamp(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty ISO-8601 value")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")


def _validate_kind_status(kind: str, status: str) -> None:
    if kind == "terminal" and status not in TERMINAL_STATUSES:
        raise ValueError("terminal kind requires a terminal status")
    if kind != "terminal" and status in {"cancelled", "interrupted"}:
        raise ValueError("cancelled or interrupted status requires terminal kind")


def _validate_identifier(field: str, value: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"invalid {field}")
    return value


def _event_from_row(row: sqlite3.Row, session_id: str) -> SessionEvent:
    if row["session_id"] != session_id:
        raise SessionEventJournalCorruptionError("stored session_id does not match journal")
    try:
        payload = json.loads(
            str(row["payload_json"]),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            object_pairs_hook=_unique_object,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SessionEventJournalCorruptionError("stored payload is not strict JSON") from exc
    if not isinstance(payload, dict) or not _all_numbers_finite(payload):
        raise SessionEventJournalCorruptionError("stored payload must be a JSON object")
    try:
        event_id = _validate_identifier("event_id", str(row["event_id"]))
        run_id = _validate_identifier("run_id", str(row["run_id"]))
    except ValueError as exc:
        raise SessionEventJournalCorruptionError("stored event identifiers are invalid") from exc
    kind = str(row["kind"])
    status = str(row["status"])
    if kind not in _KINDS or status not in _STATUSES:
        raise SessionEventJournalCorruptionError("stored kind or status is invalid")
    try:
        _validate_kind_status(kind, status)
        _validate_timestamp(str(row["timestamp"]))
    except ValueError as exc:
        raise SessionEventJournalCorruptionError(
            "stored timestamp or kind/status combination is invalid"
        ) from exc
    return SessionEvent(
        event_id=event_id,
        session_id=session_id,
        run_id=run_id,
        sequence=int(row["sequence"]),
        kind=kind,
        status=status,
        timestamp=str(row["timestamp"]),
        payload=payload,
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _all_numbers_finite(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_all_numbers_finite(item) for item in value.values())
    if isinstance(value, list):
        return all(_all_numbers_finite(item) for item in value)
    return True


def _strict_json_value(value: Any) -> bool:
    if value is None or isinstance(value, str | bool | int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_strict_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _strict_json_value(item)
            for key, item in value.items()
        )
    return False


def _schema_objects(connection: sqlite3.Connection) -> list[tuple[str, str, str | None]]:
    return connection.execute(
        "SELECT type, name, sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_autoindex%' "
        "ORDER BY type, name"
    ).fetchall()


def _validate_schema(
    connection: sqlite3.Connection, path: Path, *, lease_version: int = 4
) -> None:
    expected_columns = [
        "sequence", "event_id", "session_id", "run_id", "kind", "status", "timestamp",
        "payload_json",
    ]
    columns = [str(row[1]) for row in connection.execute("PRAGMA table_info(session_events)")]
    if columns != expected_columns:
        raise SessionEventJournalError(f"invalid session event schema at {path}")
    expected_objects = {
        ("index", "session_events_run_idx"),
        ("index", "session_events_terminal_idx"),
        ("table", "session_events"),
        ("table", "sqlite_sequence"),
    }
    if lease_version:
        expected_objects.add(("table", "active_run_lease"))
    if lease_version >= 3:
        expected_objects.add(("table", "run_fence_state"))
    objects = _schema_objects(connection)
    allowed_internal = {("table", "sqlite_stat1"), ("table", "sqlite_stat4")}
    actual_objects = {
        (kind, name) for kind, name, _ in objects if (kind, name) not in allowed_internal
    }
    if actual_objects != expected_objects:
        raise SessionEventJournalError(f"unexpected session event schema objects at {path}")
    object_sql = {(kind, name): sql for kind, name, sql in objects}
    if _normalize_sql(object_sql[("table", "session_events")]) != _normalize_sql(_EVENTS_SQL):
        raise SessionEventJournalError(f"non-canonical session event schema at {path}")
    if _normalize_sql(object_sql[("index", "session_events_run_idx")]) != _normalize_sql(
        _RUN_INDEX_SQL
    ):
        raise SessionEventJournalError(f"non-canonical session event run index at {path}")
    expected_lease_sql = {
        2: _LEASE_V2_SQL,
        3: _LEASE_V3_SQL,
        4: _LEASE_SQL,
    }.get(lease_version, _LEASE_SQL)
    if lease_version and _normalize_sql(
        object_sql[("table", "active_run_lease")]
    ) != _normalize_sql(expected_lease_sql):
        raise SessionEventJournalError(f"non-canonical active run lease schema at {path}")
    if lease_version >= 3 and _normalize_sql(
        object_sql[("table", "run_fence_state")]
    ) != _normalize_sql(_FENCE_SQL):
        raise SessionEventJournalError(f"non-canonical run fence schema at {path}")
    run_columns = [row[2] for row in connection.execute("PRAGMA index_info(session_events_run_idx)")]
    if run_columns != ["run_id", "sequence"]:
        raise SessionEventJournalError(f"invalid session event run index at {path}")
    terminal_sql = next(
        sql for kind, name, sql in _schema_objects(connection)
        if kind == "index" and name == "session_events_terminal_idx"
    )
    normalized = re.sub(r"\s+", "", terminal_sql or "").casefold()
    if normalized != re.sub(r"\s+", "", _TERMINAL_INDEX_SQL).casefold():
        raise SessionEventJournalError(f"invalid session event terminal index at {path}")


def _normalize_sql(sql: str | None) -> str:
    return re.sub(r"\s+", "", sql or "").casefold()


def _allocate_fencing_token(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT next_token FROM run_fence_state WHERE state_key = 1"
    ).fetchone()
    if row is None or int(row[0]) < 1:
        raise SessionEventJournalCorruptionError("run fence state is invalid")
    token = int(row[0])
    connection.execute(
        "UPDATE run_fence_state SET next_token = ? WHERE state_key = 1", (token + 1,)
    )
    return token


def _assert_run_lease(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    owner_id: str | None,
    fencing_token: int | None,
) -> None:
    if owner_id is None and fencing_token is None:
        active = connection.execute(
            "SELECT 1 FROM active_run_lease WHERE lease_key = 1 AND run_id = ?",
            (run_id,),
        ).fetchone()
        if active is None:
            return
        raise SessionRunLeaseLostError(
            f"run lease owner and fencing token are required for {run_id}"
        )
    if owner_id is None or fencing_token is None or fencing_token < 1:
        raise ValueError("lease owner and positive fencing token must be provided together")
    validated_owner = _validate_identifier("lease_owner_id", owner_id)
    row = connection.execute(
        "SELECT 1 FROM active_run_lease WHERE lease_key = 1 AND run_id = ? "
        "AND owner_id = ? AND fencing_token = ?",
        (run_id, validated_owner, fencing_token),
    ).fetchone()
    if row is None:
        raise SessionRunLeaseLostError(
            f"run lease owner or fencing token was lost for {run_id}"
        )


def _is_busy_or_locked(exc: sqlite3.OperationalError) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    primary = int(code) & 0xFF if isinstance(code, int) else None
    return primary in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or any(
        marker in str(exc).casefold() for marker in ("busy", "locked")
    )


def _pid_is_alive(pid: int) -> bool:
    if pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _trusted_root(root: Path) -> Path:
    _reject_untrusted_ancestor_symlinks(root)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    metadata = root.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"trusted root must not be a symlink: {root}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"trusted root is not a directory: {root}")
    root_fd = os.open(root, _DIRECTORY_FLAGS)
    try:
        opened = os.fstat(root_fd)
        if opened.st_uid != os.geteuid():
            raise ValueError(f"trusted root must be owned by the service user: {root}")
        if stat.S_IMODE(opened.st_mode) & 0o022:
            raise ValueError(f"trusted root permissions are unsafe: {root}")
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError(f"trusted root changed while opening: {root}")
        os.fchmod(root_fd, 0o700)
        os.fsync(root_fd)
        return root.resolve(strict=True)
    finally:
        os.close(root_fd)


def _reject_untrusted_ancestor_symlinks(root: Path) -> None:
    current = Path(root.absolute().anchor)
    for component in root.absolute().parts[1:]:
        current /= component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(metadata.st_mode) and metadata.st_uid != 0:
            raise ValueError(f"untrusted symlink in storage root path: {current}")


def _open_directory(
    root: Path,
    components: tuple[str, ...],
    *,
    create: bool,
    tighten: bool,
) -> int:
    directory_fd = os.open(root, _DIRECTORY_FLAGS)
    try:
        _validate_directory(directory_fd, "storage root")
        for component in components:
            created = False
            if create:
                try:
                    os.mkdir(component, mode=0o700, dir_fd=directory_fd)
                    created = True
                except FileExistsError:
                    pass
            if created:
                os.fsync(directory_fd)
            try:
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError(f"symlink path component rejected: {component}") from exc
                raise
            _validate_directory(next_fd, component)
            if tighten:
                os.fchmod(next_fd, 0o700)
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except Exception:
        os.close(directory_fd)
        raise


def _validate_directory(directory_fd: int, name: str) -> None:
    metadata = os.fstat(directory_fd)
    if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
        raise ValueError(f"directory ownership is unsafe: {name}")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ValueError(f"directory permissions are unsafe: {name}")


def _prepare_database_file(directory_fd: int) -> None:
    created = False
    try:
        database_fd = os.open(
            _DATABASE_NAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
            0o600,
            dir_fd=directory_fd,
        )
        created = True
    except FileExistsError:
        database_fd = _open_verified_file(directory_fd, _DATABASE_NAME)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError("symlink session event database rejected") from exc
        raise
    try:
        _validate_file(database_fd, _DATABASE_NAME)
        os.fchmod(database_fd, 0o600)
        if created:
            os.fsync(database_fd)
    finally:
        os.close(database_fd)
    if created:
        os.fsync(directory_fd)
    for suffix in _SIDECAR_SUFFIXES:
        _secure_optional_file(directory_fd, _DATABASE_NAME + suffix)


def _open_verified_file(directory_fd: int, name: str) -> int:
    try:
        file_fd = os.open(name, os.O_RDWR | _FILE_FLAGS, dir_fd=directory_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"symlink file rejected: {name}") from exc
        raise
    try:
        _validate_file(file_fd, name)
    except Exception:
        os.close(file_fd)
        raise
    return file_fd


def _secure_optional_file(directory_fd: int, name: str) -> None:
    try:
        file_fd = _open_optional_file(directory_fd, name)
    except FileNotFoundError:
        return
    try:
        os.fchmod(file_fd, 0o600)
    finally:
        os.close(file_fd)


def _open_optional_file(directory_fd: int, name: str) -> int:
    try:
        file_fd = os.open(name, os.O_RDWR | _FILE_FLAGS, dir_fd=directory_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"symlink file rejected: {name}") from exc
        raise
    try:
        _validate_optional_file(file_fd, name)
    except Exception:
        os.close(file_fd)
        raise
    return file_fd


def _validate_optional_file(file_fd: int, name: str) -> None:
    metadata = os.fstat(file_fd)
    # SQLite may unlink WAL/SHM after open; that safe descriptor then has nlink=0.
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink > 1:
        raise ValueError(f"sidecar must be at most one regular inode: {name}")


def _validate_file(file_fd: int, name: str) -> None:
    metadata = os.fstat(file_fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError(f"file must be one regular inode: {name}")
