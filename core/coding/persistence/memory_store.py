"""Workspace-scoped SQLite storage for durable memory proposals and facts."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_CONTENT = 32_000
MAX_CANDIDATES = 256
MAX_JSON_BYTES = 512 * 1024
_TOPICS = {"project-conventions", "decisions"}
_DATABASE_NAME = "memory.sqlite3"
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_CLOEXEC | os.O_NOFOLLOW
_SIDECAR_SUFFIXES = ("-wal", "-shm")

_FACTS_SQL = """
CREATE TABLE memory_facts (
    content_hash TEXT PRIMARY KEY, content TEXT NOT NULL,
    topic TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT NOT NULL,
    created_at TEXT NOT NULL, proposal_id TEXT NOT NULL DEFAULT ''
)
"""
_PROPOSALS_SQL = """
CREATE TABLE memory_proposals (
    proposal_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
    candidates_json TEXT NOT NULL, status TEXT NOT NULL,
    projection_status TEXT NOT NULL DEFAULT 'pending',
    revision INTEGER NOT NULL, session_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '', reflection_id TEXT NOT NULL DEFAULT '',
    base_revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
)
"""
_EVENTS_SQL = """
CREATE TABLE memory_events (
    event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
    proposal_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
    session_id TEXT NOT NULL DEFAULT '', run_id TEXT NOT NULL DEFAULT '',
    reflection_id TEXT NOT NULL DEFAULT '', candidate_count INTEGER NOT NULL,
    base_revision INTEGER NOT NULL, revision INTEGER NOT NULL,
    created_at TEXT NOT NULL
)
"""
_EVENT_INDEX_SQL = """
CREATE INDEX memory_events_proposal_idx
    ON memory_events(proposal_id, created_at)
"""


class MemoryStoreError(RuntimeError):
    """Base error for memory persistence."""


class MemoryCorruptionError(MemoryStoreError):
    """Raised when the SQLite memory evidence is malformed or tampered."""


class MemoryConflictError(MemoryStoreError):
    """Raised when a proposal revision or fact conflicts."""


@dataclass(frozen=True)
class MemoryCandidate:
    content: str
    topic: str = "project-conventions"
    source: str = "dream_proposal"
    source_ref: str = ""
    created_at: str = ""

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.strip().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryProposal:
    proposal_id: str
    workspace_id: str
    candidates: tuple[MemoryCandidate, ...]
    status: str
    revision: int
    projection_status: str = "pending"
    session_id: str = ""
    run_id: str = ""
    reflection_id: str = ""
    base_revision: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class MemoryEvent:
    event_id: str
    event_type: str
    proposal_id: str
    workspace_id: str
    session_id: str = ""
    run_id: str = ""
    reflection_id: str = ""
    candidate_count: int = 0
    base_revision: int = 0
    revision: int = 0
    created_at: str = ""


class MemoryStore:
    """Small transactional SQLite store scoped to one workspace.

    ``storage_root`` is a server-owned trust boundary. Link and inode checks fail
    closed for accidental or pre-positioned attacks; same-user concurrent writes
    inside that trusted directory remain outside this store's threat model.
    """

    def __init__(self, storage_root: Path, workspace_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", workspace_id):
            raise ValueError("invalid workspace id")
        self._root = _trusted_root(storage_root)
        self._components = ("memory", workspace_id)
        self.root = self._root.joinpath(*self._components)
        self.path = self.root / _DATABASE_NAME
        directory_fd = _open_directory(self._root, self._components)
        try:
            _prepare_database_file(directory_fd)
        finally:
            os.close(directory_fd)
        self._init_db()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        expected = self._verify_database_and_sidecars()
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self.path, timeout=5)
            self._verify_connected_inode(expected)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=FULL")
            yield conn
        finally:
            try:
                if conn is not None:
                    conn.close()
            finally:
                self._verify_database_and_sidecars()

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                version = int(db.execute("PRAGMA user_version").fetchone()[0])
                if version not in {0, SCHEMA_VERSION}:
                    raise MemoryStoreError(f"unsupported memory schema version {version}")
                objects = _schema_objects(db)
                if version == 0:
                    if objects:
                        _migrate_legacy_v0(db, objects)
                    else:
                        db.execute(_FACTS_SQL)
                        db.execute(_PROPOSALS_SQL)
                        db.execute(_EVENTS_SQL)
                        db.execute(_EVENT_INDEX_SQL)
                    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                _validate_schema(db, self.path)
                integrity = db.execute("PRAGMA integrity_check").fetchall()
                if [tuple(row) for row in integrity] != [("ok",)]:
                    raise MemoryCorruptionError(
                        f"memory database failed integrity check at {self.path}"
                    )
                with suppress(OSError):
                    os.chmod(self.path, 0o600)
                db.commit()
            except Exception:
                if db.in_transaction:
                    db.rollback()
                raise

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
            raise MemoryCorruptionError("memory database path is unsafe") from exc

    def _verify_connected_inode(self, expected: tuple[int, int]) -> None:
        actual = self._verify_database_and_sidecars()
        if actual != expected:
            raise MemoryCorruptionError("memory database changed while opening")

    def create_proposal(
        self,
        candidates: Iterable[MemoryCandidate],
        *,
        session_id: str = "",
        run_id: str = "",
        reflection_id: str = "",
        proposal_id: str | None = None,
    ) -> MemoryProposal:
        values = tuple(c for c in candidates if c.content.strip())
        if not values:
            raise ValueError("proposal requires at least one candidate")
        if len(values) > MAX_CANDIDATES:
            raise ValueError("proposal has too many candidates")
        # Deduplicate within a proposal while preserving order.
        unique: list[MemoryCandidate] = []
        seen: set[str] = set()
        for candidate in values:
            if len(candidate.content) > MAX_CONTENT or candidate.topic not in _TOPICS:
                raise ValueError("invalid memory candidate")
            if candidate.content_hash not in seen:
                unique.append(candidate)
                seen.add(candidate.content_hash)
        now = _now()
        proposal = proposal_id or f"prop_{uuid.uuid4().hex}"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            base = int(db.execute("SELECT COUNT(*) FROM memory_facts").fetchone()[0])
            payload = json.dumps([c.__dict__ for c in unique], ensure_ascii=False, sort_keys=True)
            if len(payload.encode("utf-8")) > MAX_JSON_BYTES:
                raise ValueError("proposal payload too large")
            try:
                db.execute(
                    "INSERT INTO memory_proposals (proposal_id, workspace_id, candidates_json, status, projection_status, revision, session_id, run_id, reflection_id, base_revision, created_at, updated_at) VALUES (?, ?, ?, 'pending', 'pending', 0, ?, ?, ?, ?, ?, ?)",
                    (
                        proposal,
                        self._workspace_id(),
                        payload,
                        session_id,
                        run_id,
                        reflection_id,
                        base,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                existing = db.execute(
                    "SELECT * FROM memory_proposals WHERE proposal_id=?", (proposal,)
                ).fetchone()
                if (
                    existing is None
                    or existing["candidates_json"] != payload
                    or any(
                        existing[key] != value
                        for key, value in {
                            "session_id": session_id,
                            "run_id": run_id,
                            "reflection_id": reflection_id,
                        }.items()
                    )
                ):
                    raise MemoryConflictError(f"proposal id conflict: {proposal}") from exc
                db.rollback()
                return _proposal(existing)
            self._event(
                db,
                "proposal_created",
                proposal,
                self._workspace_id(),
                session_id,
                run_id,
                reflection_id,
                len(unique),
                base,
                0,
                now,
            )
            db.commit()
        return self.get_proposal(proposal)  # type: ignore[return-value]

    def get_proposal(self, proposal_id: str) -> MemoryProposal | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM memory_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
        return _proposal(row) if row else None

    def mark_projection_complete(self, proposal_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE memory_proposals SET projection_status='complete', updated_at=? WHERE proposal_id=? AND status='approved'",
                (_now(), proposal_id),
            )
            db.commit()

    def pending_projections(self) -> list[MemoryProposal]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM memory_proposals WHERE status='approved' AND projection_status!='complete'"
            ).fetchall()
        return [_proposal(row) for row in rows]

    def list_proposals(self, status: str | None = None) -> list[MemoryProposal]:
        with self._connect() as db:
            query = "SELECT * FROM memory_proposals"
            args: tuple[Any, ...] = ()
            if status:
                query += " WHERE status=?"
                args = (status,)
            query += " ORDER BY created_at"
            rows = db.execute(query, args).fetchall()
        return [_proposal(r) for r in rows]

    def approve(self, proposal_id: str, expected_revision: int) -> MemoryProposal:
        return self._transition(proposal_id, expected_revision, "approved")

    def reject(self, proposal_id: str, expected_revision: int) -> MemoryProposal:
        return self._transition(proposal_id, expected_revision, "rejected")

    def _transition(self, proposal_id: str, expected: int, status: str) -> MemoryProposal:
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM memory_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
            if row is None:
                raise KeyError(proposal_id)
            current = _proposal(row)
            if current.revision != expected:
                raise MemoryConflictError(
                    f"proposal revision conflict: expected {expected}, got {current.revision}"
                )
            current_fact_count = int(db.execute("SELECT COUNT(*) FROM memory_facts").fetchone()[0])
            if (
                status == "approved"
                and current.status == "pending"
                and current_fact_count != current.base_revision
            ):
                raise MemoryConflictError("proposal base revision is stale")
            if current.status != "pending":
                if current.status == status:
                    db.commit()
                    return current
                raise MemoryConflictError(f"proposal already {current.status}")
            new_revision = current.revision + 1
            if status == "approved":
                for candidate in current.candidates:
                    db.execute(
                        "INSERT OR IGNORE INTO memory_facts VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            candidate.content_hash,
                            candidate.content,
                            candidate.topic,
                            candidate.source,
                            candidate.source_ref,
                            candidate.created_at or now,
                            proposal_id,
                        ),
                    )
            db.execute(
                "UPDATE memory_proposals SET status=?, projection_status=?, revision=?, updated_at=? WHERE proposal_id=?",
                (
                    status,
                    "pending" if status == "approved" else "complete",
                    new_revision,
                    now,
                    proposal_id,
                ),
            )
            self._event(
                db,
                f"proposal_{status}",
                proposal_id,
                current.workspace_id,
                current.session_id,
                current.run_id,
                current.reflection_id,
                len(current.candidates),
                current.base_revision,
                new_revision,
                now,
            )
            db.commit()
        return self.get_proposal(proposal_id)  # type: ignore[return-value]

    def list_facts(self) -> list[MemoryCandidate]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT f.content, f.topic, f.source, f.source_ref, f.created_at FROM memory_facts f JOIN memory_proposals p ON p.proposal_id=f.proposal_id WHERE p.status='approved' ORDER BY f.rowid"
            ).fetchall()
        return [MemoryCandidate(**dict(r)) for r in rows]

    def list_events(self, proposal_id: str | None = None) -> list[MemoryEvent]:
        with self._connect() as db:
            if proposal_id:
                rows = db.execute(
                    "SELECT * FROM memory_events WHERE proposal_id=? ORDER BY rowid", (proposal_id,)
                ).fetchall()
            else:
                rows = db.execute("SELECT * FROM memory_events ORDER BY rowid").fetchall()
        return [MemoryEvent(**dict(r)) for r in rows]

    def _workspace_id(self) -> str:
        return self.root.name

    @staticmethod
    def _event(
        db: sqlite3.Connection,
        event_type: str,
        proposal_id: str,
        workspace_id: str,
        session_id: str,
        run_id: str,
        reflection_id: str,
        count: int,
        base: int,
        revision: int,
        now: str,
    ) -> None:
        db.execute(
            "INSERT INTO memory_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                event_type,
                proposal_id,
                workspace_id,
                session_id,
                run_id,
                reflection_id,
                count,
                base,
                revision,
                now,
            ),
        )


def _proposal(row: sqlite3.Row) -> MemoryProposal:
    data = dict(row)
    try:
        encoded = data.pop("candidates_json")
        if type(encoded) is not str:
            raise TypeError("candidates_json is not text")
        if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
            raise ValueError("candidates_json is too large")
        raw_candidates = json.loads(
            encoded,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(raw_candidates, list) or not (1 <= len(raw_candidates) <= MAX_CANDIDATES):
            raise TypeError("candidates_json must be a non-empty bounded list")
        candidates_list: list[MemoryCandidate] = []
        expected = {"content", "topic", "source", "source_ref", "created_at"}
        for item in raw_candidates:
            if not isinstance(item, dict) or not set(item).issubset(expected):
                raise TypeError("candidate must be an object with known fields")
            candidate = MemoryCandidate(**item)
            if any(
                type(value) is not str
                for value in (
                    candidate.content,
                    candidate.topic,
                    candidate.source,
                    candidate.source_ref,
                    candidate.created_at,
                )
            ):
                raise TypeError("candidate fields must be strings")
            if (
                not candidate.content.strip()
                or len(candidate.content) > MAX_CONTENT
                or candidate.topic not in _TOPICS
            ):
                raise ValueError("invalid memory candidate")
            candidates_list.append(candidate)
        candidates = tuple(candidates_list)
    except (ValueError, TypeError, KeyError) as exc:
        raise MemoryCorruptionError("invalid memory proposal payload") from exc
    data["candidates"] = candidates
    try:
        return MemoryProposal(**data)
    except (TypeError, ValueError) as exc:
        raise MemoryCorruptionError("invalid memory proposal row") from exc


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _schema_objects(db: sqlite3.Connection) -> list[tuple[str, str, str, str | None]]:
    return db.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
    ).fetchall()


def _normalize_sql(sql: str | None) -> str:
    return re.sub(r"\s+", "", sql or "").casefold()


def _validate_schema(db: sqlite3.Connection, path: Path) -> None:
    objects = _schema_objects(db)
    expected_sql = {
        ("table", "memory_facts"): _FACTS_SQL,
        ("table", "memory_proposals"): _PROPOSALS_SQL,
        ("table", "memory_events"): _EVENTS_SQL,
        ("index", "memory_events_proposal_idx"): _EVENT_INDEX_SQL,
        ("index", "sqlite_autoindex_memory_facts_1"): None,
        ("index", "sqlite_autoindex_memory_proposals_1"): None,
        ("index", "sqlite_autoindex_memory_events_1"): None,
    }
    actual = {(kind, name): sql for kind, name, _, sql in objects}
    if set(actual) != set(expected_sql):
        raise MemoryStoreError(f"unexpected memory schema objects at {path}")
    for key, sql in expected_sql.items():
        if sql is None:
            if actual[key] is not None:
                raise MemoryStoreError(f"invalid generated memory index at {path}")
        elif _normalize_sql(actual[key]) != _normalize_sql(sql):
            raise MemoryStoreError(f"non-canonical memory schema object {key[1]} at {path}")
    event_index = db.execute("PRAGMA index_info(memory_events_proposal_idx)").fetchall()
    if [row[2] for row in event_index] != ["proposal_id", "created_at"]:
        raise MemoryStoreError(f"invalid memory event index at {path}")


def _migrate_legacy_v0(
    db: sqlite3.Connection, objects: list[tuple[str, str, str, str | None]]
) -> None:
    """Migrate the previous three-table schema without accepting unknown objects."""
    names = {(kind, name) for kind, name, _, _ in objects}
    allowed = {
        ("table", "memory_facts"),
        ("table", "memory_proposals"),
        ("table", "memory_events"),
        ("index", "memory_events_proposal_idx"),
        ("index", "sqlite_autoindex_memory_facts_1"),
        ("index", "sqlite_autoindex_memory_proposals_1"),
        ("index", "sqlite_autoindex_memory_events_1"),
    }
    if names != allowed:
        raise MemoryStoreError("unknown objects in legacy memory database")
    proposal_columns = {row[1] for row in db.execute("PRAGMA table_info(memory_proposals)")}
    if "projection_status" in proposal_columns:
        return
    db.execute("ALTER TABLE memory_proposals RENAME TO memory_proposals_legacy")
    db.execute("DROP INDEX memory_events_proposal_idx")
    db.execute(_PROPOSALS_SQL)
    db.execute(
        "INSERT INTO memory_proposals (proposal_id, workspace_id, candidates_json, status, projection_status, revision, session_id, run_id, reflection_id, base_revision, created_at, updated_at) SELECT proposal_id, workspace_id, candidates_json, status, 'pending', revision, session_id, run_id, reflection_id, base_revision, created_at, updated_at FROM memory_proposals_legacy"
    )
    db.execute("DROP TABLE memory_proposals_legacy")
    db.execute(_EVENT_INDEX_SQL)


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
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError(f"trusted root changed while opening: {root}")
        if opened.st_uid != os.geteuid():
            raise ValueError(f"trusted root must be owned by the service user: {root}")
        os.fchmod(root_fd, 0o700)
        os.fsync(root_fd)
        resolved = root.resolve(strict=True)
        metadata = resolved.stat()
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError(f"trusted root escaped while resolving: {root}")
        return resolved
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
            raise ValueError(f"untrusted symlink in memory root path: {current}")


def _open_directory(root: Path, components: tuple[str, ...], *, tighten: bool = True) -> int:
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
                if tighten:
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
            raise ValueError("symlink memory database rejected") from exc
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
        file_fd = _open_verified_file(directory_fd, name)
    except FileNotFoundError:
        return
    try:
        os.fchmod(file_fd, 0o600)
    finally:
        os.close(file_fd)


def _validate_file(file_fd: int, name: str) -> None:
    metadata = os.fstat(file_fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError(f"file must be one regular inode: {name}")


def _now() -> str:
    from core.coding.context.workspace import now

    return now()
