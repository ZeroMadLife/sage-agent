"""Durable, session-scoped browser event journal."""

from __future__ import annotations

import errno
import json
import math
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
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


class SessionEventJournalError(RuntimeError):
    """Base error for event journal persistence."""


class SessionEventJournalCorruptionError(SessionEventJournalError):
    """The journal path, database, schema, or stored data is unsafe."""


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


class SessionEventJournal:
    """SQLite event journal bound to one server-owned session directory."""

    def __init__(self, storage_root: Path, session_id: str) -> None:
        self.session_id = _validate_identifier("session_id", session_id)
        self._root = _trusted_root(storage_root)
        self._components = ("evidence", self.session_id)
        self.root = self._root.joinpath(*self._components)
        self.path = self.root / _DATABASE_NAME
        directory_fd = _open_directory(self._root, self._components)
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
                row = connection.execute(
                    "SELECT * FROM session_events WHERE run_id = ? "
                    "AND kind = 'terminal' "
                    "ORDER BY sequence LIMIT 1",
                    (values["run_id"],),
                ).fetchone()
                if row is not None:
                    connection.commit()
                    return _event_from_row(row, self.session_id)
                stored = self._insert(connection, **values)
                connection.commit()
                return stored
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                with self._connect() as retry:
                    row = retry.execute(
                        "SELECT * FROM session_events WHERE run_id = ? "
                        "AND kind = 'terminal'",
                        (values["run_id"],),
                    ).fetchone()
                    if row is None:
                        raise SessionEventJournalError("terminal event conflict") from exc
                    return _event_from_row(row, self.session_id)

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
                "THEN 1 ELSE 0 END) = 0 ORDER BY MIN(sequence)"
            ).fetchall()
        return tuple(str(row[0]) for row in rows)

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
            connection = sqlite3.connect(self.path, timeout=5)
            self._verify_connected_inode(expected)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
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
            directory_fd = _open_directory(self._root, self._components)
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
    if kind == "terminal" and status not in TERMINAL_STATUSES:
        raise ValueError("terminal kind requires a terminal status")
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
    actual_event_id = event_id or str(uuid.uuid4())
    _validate_identifier("event_id", actual_event_id)
    actual_timestamp = timestamp or datetime.now(UTC).isoformat()
    try:
        parsed = datetime.fromisoformat(actual_timestamp)
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return {
        "run_id": validated_run,
        "kind": kind,
        "status": status,
        "payload_json": payload_json,
        "event_id": actual_event_id,
        "timestamp": actual_timestamp,
    }


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


def _validate_schema(connection: sqlite3.Connection, path: Path) -> None:
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
    actual_objects = {(kind, name) for kind, name, _ in _schema_objects(connection)}
    if actual_objects != expected_objects:
        raise SessionEventJournalError(f"unexpected session event schema objects at {path}")
    object_sql = {(kind, name): sql for kind, name, sql in _schema_objects(connection)}
    if _normalize_sql(object_sql[("table", "session_events")]) != _normalize_sql(_EVENTS_SQL):
        raise SessionEventJournalError(f"non-canonical session event schema at {path}")
    if _normalize_sql(object_sql[("index", "session_events_run_idx")]) != _normalize_sql(
        _RUN_INDEX_SQL
    ):
        raise SessionEventJournalError(f"non-canonical session event run index at {path}")
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


def _open_directory(root: Path, components: tuple[str, ...]) -> int:
    directory_fd = os.open(root, _DIRECTORY_FLAGS)
    try:
        for component in components:
            created = False
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
            os.fchmod(next_fd, 0o700)
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except Exception:
        os.close(directory_fd)
        raise


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
