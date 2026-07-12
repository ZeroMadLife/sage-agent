"""Exact schema creation, validation, and migration for SQLite transcripts."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2
V1_CREATE_TABLE_SQL = """
CREATE TABLE transcript (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    call_id TEXT NOT NULL DEFAULT '',
    artifact_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
)
"""
CANONICAL_CREATE_TABLE_SQL = """
CREATE TABLE transcript (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    turn_id TEXT NOT NULL DEFAULT '',
    call_id TEXT NOT NULL DEFAULT '',
    artifact_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    args_json TEXT NOT NULL DEFAULT '{}',
    is_error INTEGER NOT NULL DEFAULT 0 CHECK(is_error IN (0,1)),
    policy_reason TEXT NOT NULL DEFAULT '',
    security_event_type TEXT NOT NULL DEFAULT ''
)
"""

_V1_COLUMNS = [
    ("sequence", "INTEGER", 0, None, 1),
    ("message_id", "TEXT", 1, None, 0),
    ("role", "TEXT", 1, None, 0),
    ("content", "TEXT", 1, None, 0),
    ("run_id", "TEXT", 1, "''", 0),
    ("turn_id", "TEXT", 1, "''", 0),
    ("call_id", "TEXT", 1, "''", 0),
    ("artifact_ref", "TEXT", 1, "''", 0),
    ("created_at", "TEXT", 1, "''", 0),
]
_V2_COLUMN_DEFINITIONS = (
    "name TEXT NOT NULL DEFAULT ''",
    "args_json TEXT NOT NULL DEFAULT '{}'",
    "is_error INTEGER NOT NULL DEFAULT 0 CHECK(is_error IN (0,1))",
    "policy_reason TEXT NOT NULL DEFAULT ''",
    "security_event_type TEXT NOT NULL DEFAULT ''",
)
_V2_COLUMNS = [
    *_V1_COLUMNS,
    ("name", "TEXT", 1, "''", 0),
    ("args_json", "TEXT", 1, "'{}'", 0),
    ("is_error", "INTEGER", 1, "0", 0),
    ("policy_reason", "TEXT", 1, "''", 0),
    ("security_event_type", "TEXT", 1, "''", 0),
]


class TranscriptStoreError(RuntimeError):
    """Base error for transcript persistence failures."""


class TranscriptSchemaError(TranscriptStoreError):
    """Raised when an existing transcript schema is incompatible."""


def initialize_or_validate(connection: sqlite3.Connection, path: Path) -> None:
    """Create v2 or transactionally migrate an exact v1 database."""
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version not in {0, 1, SCHEMA_VERSION}:
        raise TranscriptSchemaError(f"unsupported transcript schema version {version} at {path}")

    if version == 0:
        objects = _schema_objects(connection)
        if objects:
            names = ", ".join(f"{kind}:{name}" for kind, name, _, _ in objects)
            raise TranscriptSchemaError(
                f"refusing to initialize non-empty v0 transcript database at {path}: {names}"
            )
        connection.execute(CANONICAL_CREATE_TABLE_SQL)
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    elif version == 1:
        _validate_schema(connection, path, V1_CREATE_TABLE_SQL, _V1_COLUMNS)
        for definition in _V2_COLUMN_DEFINITIONS:
            _add_v2_column(connection, definition)
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    _validate_schema(connection, path, CANONICAL_CREATE_TABLE_SQL, _V2_COLUMNS)


def _add_v2_column(connection: sqlite3.Connection, definition: str) -> None:
    """Add one migration column; kept narrow for deterministic fault injection."""
    connection.execute(f"ALTER TABLE transcript ADD COLUMN {definition}")


def _schema_objects(
    connection: sqlite3.Connection,
) -> list[tuple[str, str, str, str | None]]:
    return connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_schema
        ORDER BY type, name
        """
    ).fetchall()


def _validate_schema(
    connection: sqlite3.Connection,
    path: Path,
    expected_sql: str,
    expected_columns: list[tuple[str, str, int, str | None, int]],
) -> None:
    objects = _schema_objects(connection)
    transcript_tables = [entry for entry in objects if entry[:2] == ("table", "transcript")]
    if len(transcript_tables) != 1:
        raise TranscriptSchemaError(f"invalid transcript table at {path}")
    if _normalize_sql(transcript_tables[0][3]) != _normalize_sql(expected_sql):
        raise TranscriptSchemaError(f"non-canonical transcript table SQL at {path}")

    columns = connection.execute("PRAGMA table_info(transcript)").fetchall()
    if [tuple(column[1:]) for column in columns] != expected_columns:
        raise TranscriptSchemaError(f"invalid transcript columns at {path}")

    indexes = connection.execute("PRAGMA index_list(transcript)").fetchall()
    if len(indexes) != 1:
        raise TranscriptSchemaError(f"invalid transcript indexes at {path}")
    index = indexes[0]
    index_name = str(index[1])
    index_columns = connection.execute(
        "SELECT name FROM pragma_index_info(?) ORDER BY seqno", (index_name,)
    ).fetchall()
    if (
        int(index[2]) != 1
        or str(index[3]) != "u"
        or int(index[4]) != 0
        or index_columns != [("message_id",)]
    ):
        raise TranscriptSchemaError(f"invalid transcript unique index at {path}")

    allowed = {
        ("table", "transcript", "transcript"),
        ("table", "sqlite_sequence", "sqlite_sequence"),
        ("index", index_name, "transcript"),
    }
    actual = {(kind, name, table) for kind, name, table, _ in objects}
    if actual != allowed:
        extras = sorted(actual - allowed)
        raise TranscriptSchemaError(f"unexpected transcript schema objects at {path}: {extras}")

    object_map = {(kind, name): sql for kind, name, _, sql in objects}
    if object_map[("index", index_name)] is not None:
        raise TranscriptSchemaError(f"transcript unique index is not SQLite-generated at {path}")
    if _normalize_sql(object_map[("table", "sqlite_sequence")]) != _normalize_sql(
        "CREATE TABLE sqlite_sequence(name,seq)"
    ):
        raise TranscriptSchemaError(f"invalid SQLite sequence table at {path}")


def _normalize_sql(sql: str | None) -> str:
    return re.sub(r"\s+", "", sql or "").casefold()
