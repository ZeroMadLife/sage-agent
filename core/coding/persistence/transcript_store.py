"""Canonical SQLite transcript persistence with explicit audit export."""

from __future__ import annotations

import errno
import json
import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.coding.persistence.atomic_export import EXPORT_NAME, publish_jsonl
from core.coding.persistence.transcript_schema import (
    SCHEMA_VERSION,
    TranscriptSchemaError,
    TranscriptStoreError,
    initialize_or_validate,
)

__all__ = [
    "TranscriptConflictError",
    "TranscriptCorruptionError",
    "TranscriptItem",
    "TranscriptSchemaError",
    "TranscriptStore",
    "TranscriptStoreError",
]

_BUSY_TIMEOUT_MS = 5000
_DATABASE_NAME = "transcript.sqlite3"
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_CLOEXEC | os.O_NOFOLLOW
_SIDECAR_SUFFIXES = ("-wal", "-shm")
_STORED_COLUMNS = (
    "message_id, role, content, run_id, turn_id, call_id, artifact_ref, created_at, "
    "name, args_json, is_error, policy_reason, security_event_type"
)
_SELECT_SQL = f"SELECT sequence, {_STORED_COLUMNS} FROM transcript ORDER BY sequence"


class TranscriptCorruptionError(TranscriptStoreError):
    """Raised when the canonical SQLite database cannot be read safely."""


class TranscriptConflictError(TranscriptStoreError):
    """Raised when a message id is reused for different canonical evidence."""


@dataclass(frozen=True)
class TranscriptItem:
    """One canonical transcript entry."""

    message_id: str
    role: str
    content: str
    run_id: str = ""
    turn_id: str = ""
    call_id: str = ""
    artifact_ref: str = ""
    created_at: str = ""
    sequence: int = field(default=0, compare=False)
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    is_error: bool = False
    policy_reason: str = ""
    security_event_type: str = ""


class TranscriptStore:
    """Persist a session transcript in SQLite under a server-controlled root.

    The root is assumed to be trusted and unavailable to tools or other untrusted
    same-user writers. ``O_NOFOLLOW`` and inode/link checks prevent accidental
    traversal and common link attacks, but cannot eliminate same-user TOCTOU races
    when an attacker can mutate the trusted directory concurrently.
    """

    def __init__(self, root: Path, session_id: str) -> None:
        _validate_scope_id(session_id, "session")
        self._root = _trusted_root(root)
        self._components = ("evidence", session_id)
        self.path = self._root.joinpath(*self._components, _DATABASE_NAME)

        directory_fd = _open_directory(self._root, self._components)
        try:
            _prepare_database_file(directory_fd)
        finally:
            os.close(directory_fd)
        self._initialize_schema()

    @property
    def schema_version(self) -> int:
        """Return the schema version supported by this store."""
        return SCHEMA_VERSION

    def append(self, item: TranscriptItem) -> bool:
        """Insert ``item`` once by message id in a short transaction."""
        return self.append_and_get_sequence(item)[0]

    def append_and_get_sequence(self, item: TranscriptItem) -> tuple[bool, int]:
        """Insert canonical evidence or return its existing stable sequence."""
        args_json = _canonical_args_json(item.args)
        values = _stored_values(item, args_json)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    existing = connection.execute(
                        f"SELECT sequence, {_STORED_COLUMNS} FROM transcript WHERE message_id = ?",
                        (item.message_id,),
                    ).fetchone()
                    if existing is not None:
                        if tuple(existing[1:]) != values:
                            raise TranscriptConflictError(
                                f"conflicting transcript message_id {item.message_id!r} at {self.path}"
                            )
                        connection.commit()
                        return False, int(existing[0])
                    cursor = connection.execute(
                        """
                        INSERT INTO transcript (
                            message_id, role, content, run_id, turn_id, call_id,
                            artifact_ref, created_at, name, args_json, is_error,
                            policy_reason, security_event_type
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )
                    if cursor.lastrowid is None:
                        raise TranscriptStoreError(
                            f"transcript insert returned no sequence at {self.path}"
                        )
                    sequence = int(cursor.lastrowid)
                    connection.commit()
                    return True, sequence
                except Exception:
                    _rollback(connection)
                    raise
        except sqlite3.DatabaseError as exc:
            self._raise_if_corrupt(exc)
            raise

    def read_all(self) -> list[TranscriptItem]:
        """Read transcript entries in their insertion sequence."""
        try:
            with self._connect() as connection:
                return _read_items(connection, self.path, _SELECT_SQL)
        except sqlite3.DatabaseError as exc:
            self._raise_if_corrupt(exc)
            raise

    def read_range(self, start: int, end: int) -> list[TranscriptItem]:
        """Read an inclusive sequence range in canonical order."""
        if not isinstance(start, int) or isinstance(start, bool) or start < 1:
            raise ValueError("start must be an integer >= 1")
        if not isinstance(end, int) or isinstance(end, bool) or end < start:
            raise ValueError("end must be an integer >= start")
        sql = (
            f"SELECT sequence, {_STORED_COLUMNS} FROM transcript "
            "WHERE sequence BETWEEN ? AND ? ORDER BY sequence"
        )
        try:
            with self._connect() as connection:
                return _read_items(connection, self.path, sql, (start, end))
        except sqlite3.DatabaseError as exc:
            self._raise_if_corrupt(exc)
            raise

    def export_jsonl(self) -> Path:
        """Atomically replace the manual JSONL audit export from one snapshot."""
        directory_fd = _open_directory(self._root, (*self._components, "exports"))
        try:
            publish_jsonl(directory_fd, self._snapshot_jsonl_payload)
        finally:
            os.close(directory_fd)
        return self.path.parent / "exports" / EXPORT_NAME

    def _snapshot_jsonl_payload(self) -> bytes:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN")
                try:
                    items = _read_items(connection, self.path, _SELECT_SQL)
                    connection.commit()
                except Exception:
                    _rollback(connection)
                    raise
        except sqlite3.DatabaseError as exc:
            self._raise_if_corrupt(exc)
            raise

        return "".join(
            json.dumps(asdict(item), ensure_ascii=False, sort_keys=True) + "\n" for item in items
        ).encode("utf-8")

    def check_integrity(self) -> bool:
        """Return true for an intact database, raising on corruption."""
        try:
            with self._connect() as connection:
                results = connection.execute("PRAGMA integrity_check").fetchall()
        except sqlite3.DatabaseError as exc:
            self._raise_if_corrupt(exc)
            raise
        if results != [("ok",)]:
            details = "; ".join(str(row[0]) for row in results)
            raise TranscriptCorruptionError(
                f"transcript database failed integrity check at {self.path}: {details}"
            )
        return True

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open one configured connection and close it before returning."""
        expected = self._verify_database_and_sidecars()
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.path,
                timeout=_BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,
            )
            self._verify_connected_inode(expected)
            connection.execute(f"PRAGMA busy_timeout={int(_BUSY_TIMEOUT_MS)}")
            connection.execute("PRAGMA foreign_keys=ON")
            _ensure_wal(connection, self.path)
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        finally:
            try:
                if connection is not None:
                    connection.close()
            finally:
                self._verify_database_and_sidecars()

    def _initialize_schema(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    initialize_or_validate(connection, self.path)
                    connection.commit()
                except Exception:
                    _rollback(connection)
                    raise
        except sqlite3.OperationalError as exc:
            raise TranscriptStoreError(
                f"unable to initialize transcript schema at {self.path}: {exc}"
            ) from exc
        except sqlite3.DatabaseError as exc:
            self._raise_if_corrupt(exc)
            raise

    def _verify_database_and_sidecars(self) -> tuple[int, int]:
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

    def _verify_connected_inode(self, expected: tuple[int, int]) -> None:
        directory_fd = _open_directory(self._root, self._components)
        try:
            database_fd = _open_verified_file(directory_fd, _DATABASE_NAME)
            try:
                metadata = os.fstat(database_fd)
                actual = (metadata.st_dev, metadata.st_ino)
            finally:
                os.close(database_fd)
        finally:
            os.close(directory_fd)
        if actual != expected:
            raise ValueError(f"transcript database changed while opening: {self.path}")

    def _raise_if_corrupt(self, exc: sqlite3.DatabaseError) -> None:
        if type(exc) is sqlite3.DatabaseError:
            raise TranscriptCorruptionError(
                f"corrupt transcript database at {self.path}: {exc}"
            ) from exc


def _canonical_args_json(args: dict[str, Any]) -> str:
    if not isinstance(args, dict):
        raise TypeError("transcript args must be a dict")
    _validate_json_value(args)
    return json.dumps(
        args,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if not (value == value and abs(value) != float("inf")):
            raise ValueError("transcript args must contain finite JSON numbers")
        return
    if isinstance(value, list):
        for child in value:
            _validate_json_value(child)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError("transcript args object keys must be strings")
            _validate_json_value(child)
        return
    raise TypeError(f"transcript args contains non-JSON value {type(value).__name__}")


def _stored_values(item: TranscriptItem, args_json: str) -> tuple[Any, ...]:
    return (
        item.message_id,
        item.role,
        item.content,
        item.run_id,
        item.turn_id,
        item.call_id,
        item.artifact_ref,
        item.created_at,
        item.name,
        args_json,
        int(item.is_error),
        item.policy_reason,
        item.security_event_type,
    )


def _read_items(
    connection: sqlite3.Connection,
    path: Path,
    sql: str,
    parameters: tuple[Any, ...] = (),
) -> list[TranscriptItem]:
    return [_row_to_item(row, path) for row in connection.execute(sql, parameters).fetchall()]


def _row_to_item(row: tuple[Any, ...], path: Path) -> TranscriptItem:
    message_id = str(row[1])
    try:
        args = json.loads(row[10], parse_constant=_reject_json_constant)
        _validate_json_value(args)
    except (TypeError, ValueError) as exc:
        raise TranscriptCorruptionError(
            f"invalid args_json for transcript message {message_id!r} at {path}: {exc}"
        ) from exc
    if not isinstance(args, dict):
        raise TranscriptCorruptionError(
            f"invalid args_json object for transcript message {message_id!r} at {path}"
        )
    return TranscriptItem(
        message_id=message_id,
        role=str(row[2]),
        content=str(row[3]),
        run_id=str(row[4]),
        turn_id=str(row[5]),
        call_id=str(row[6]),
        artifact_ref=str(row[7]),
        created_at=str(row[8]),
        sequence=int(row[0]),
        name=str(row[9]),
        args=args,
        is_error=bool(row[11]),
        policy_reason=str(row[12]),
        security_event_type=str(row[13]),
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _ensure_wal(connection: sqlite3.Connection, path: Path) -> None:
    deadline = time.monotonic() + (_BUSY_TIMEOUT_MS / 1000)
    while True:
        try:
            journal_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            if journal_mode is not None and str(journal_mode[0]).lower() == "wal":
                return
            raise TranscriptStoreError(f"unable to enable WAL for transcript database {path}")
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            remaining = deadline - time.monotonic()
            time.sleep(min(0.01, max(0.0, remaining)))


def _rollback(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        connection.rollback()


def _trusted_root(root: Path) -> Path:
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    metadata = root.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"trusted root must not be a symlink: {root}")
    if not stat.S_ISDIR(metadata.st_mode):
        raise ValueError(f"trusted root is not a directory: {root}")
    root_fd = os.open(root, _DIRECTORY_FLAGS)
    try:
        opened = os.fstat(root_fd)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError(f"trusted root changed while opening: {root}")
        if opened.st_uid != os.geteuid():
            raise ValueError(f"trusted root must be owned by the service user: {root}")
        os.fchmod(root_fd, 0o700)
        os.fsync(root_fd)
        resolved = root.resolve(strict=True)
        resolved_metadata = resolved.stat()
        if (opened.st_dev, opened.st_ino) != (
            resolved_metadata.st_dev,
            resolved_metadata.st_ino,
        ):
            raise ValueError(f"trusted root escaped while resolving: {root}")
        return resolved
    finally:
        os.close(root_fd)


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
            try:
                os.fchmod(next_fd, 0o700)
                os.close(directory_fd)
            except Exception:
                os.close(next_fd)
                raise
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
            raise ValueError(f"symlink file rejected: {_DATABASE_NAME}") from exc
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


def _open_verified_file(
    directory_fd: int,
    name: str,
    *,
    flags: int = os.O_RDWR,
) -> int:
    try:
        file_fd = os.open(name, flags | _FILE_FLAGS, dir_fd=directory_fd)
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
        file_fd = os.open(name, os.O_RDWR | _FILE_FLAGS, dir_fd=directory_fd)
    except FileNotFoundError:
        return
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"symlink file rejected: {name}") from exc
        raise
    try:
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"non-regular file rejected: {name}")
        if metadata.st_nlink > 1:
            raise ValueError(f"hardlink file rejected: {name}")
        if metadata.st_nlink == 0:
            return
        os.fchmod(file_fd, 0o600)
    finally:
        os.close(file_fd)


def _validate_file(file_fd: int, name: str) -> None:
    metadata = os.fstat(file_fd)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"non-regular file rejected: {name}")
    if metadata.st_nlink != 1:
        raise ValueError(f"hardlink file rejected: {name}")


def _validate_scope_id(value: str, label: str) -> None:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"invalid {label} id")
