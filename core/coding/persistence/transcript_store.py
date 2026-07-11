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
from dataclasses import asdict, dataclass
from pathlib import Path

from core.coding.persistence.atomic_export import EXPORT_NAME, publish_jsonl
from core.coding.persistence.transcript_schema import (
    SCHEMA_VERSION,
    TranscriptSchemaError,
    TranscriptStoreError,
    initialize_or_validate,
)

__all__ = [
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
_SELECT_SQL = """
SELECT message_id, role, content, run_id, turn_id, call_id, artifact_ref, created_at
FROM transcript
ORDER BY sequence
"""


class TranscriptCorruptionError(TranscriptStoreError):
    """Raised when the canonical SQLite database cannot be read safely."""


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
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    cursor = connection.execute(
                        """
                        INSERT INTO transcript (
                            message_id, role, content, run_id, turn_id, call_id,
                            artifact_ref, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(message_id) DO NOTHING
                        """,
                        (
                            item.message_id,
                            item.role,
                            item.content,
                            item.run_id,
                            item.turn_id,
                            item.call_id,
                            item.artifact_ref,
                            item.created_at,
                        ),
                    )
                    inserted = cursor.rowcount == 1
                    connection.commit()
                    return inserted
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
                return _read_items(connection)
        except sqlite3.DatabaseError as exc:
            self._raise_if_corrupt(exc)
            raise

    def export_jsonl(self) -> Path:
        """Atomically replace the manual JSONL audit export from one snapshot."""
        try:
            with self._connect() as connection:
                connection.execute("BEGIN")
                try:
                    items = _read_items(connection)
                    connection.commit()
                except Exception:
                    _rollback(connection)
                    raise
        except sqlite3.DatabaseError as exc:
            self._raise_if_corrupt(exc)
            raise

        payload = "".join(
            json.dumps(asdict(item), ensure_ascii=False, sort_keys=True) + "\n" for item in items
        ).encode("utf-8")
        directory_fd = _open_directory(self._root, (*self._components, "exports"))
        try:
            publish_jsonl(directory_fd, payload)
        finally:
            os.close(directory_fd)
        return self.path.parent / "exports" / EXPORT_NAME

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


def _read_items(connection: sqlite3.Connection) -> list[TranscriptItem]:
    return [TranscriptItem(*row) for row in connection.execute(_SELECT_SQL).fetchall()]


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
