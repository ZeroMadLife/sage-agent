from __future__ import annotations

import json
import multiprocessing
import os
import re
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty

import pytest

from core.coding.persistence import atomic_export as atomic_export_module
from core.coding.persistence import transcript_store as transcript_module
from core.coding.persistence.transcript_store import (
    TranscriptCorruptionError,
    TranscriptItem,
    TranscriptStore,
    TranscriptStoreError,
)


def _spawn_append(
    root: str,
    ready: multiprocessing.Queue,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.Queue,
) -> None:
    store = TranscriptStore(Path(root), "s1")
    ready.put(True)
    if not start.wait(10):
        results.put(RuntimeError("spawn start timed out"))
        return
    try:
        results.put(store.append(TranscriptItem(message_id="m1", role="user", content="hello")))
    except Exception as exc:  # pragma: no cover - reported to the parent assertion
        results.put(exc)


def _spawn_export(
    root: str,
    ready: multiprocessing.Queue,
    start: multiprocessing.synchronize.Event,
    results: multiprocessing.Queue,
) -> None:
    store = TranscriptStore(Path(root), "s1")
    ready.put(True)
    if not start.wait(10):
        results.put(RuntimeError("spawn start timed out"))
        return
    try:
        path = store.export_jsonl()
        results.put(path.read_bytes())
    except Exception as exc:  # pragma: no cover - reported to the parent assertion
        results.put(exc)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _fd_count() -> int:
    return len(os.listdir("/dev/fd"))


def _precreate_database(tmp_path, schema_sql: str | None, version: int) -> Path:
    path = tmp_path / "evidence" / "s1" / "transcript.sqlite3"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        if schema_sql is not None:
            connection.execute(schema_sql)
        connection.execute(f"PRAGMA user_version={version}")
    return path


def _transcript_schema(
    *,
    sequence: str = "INTEGER PRIMARY KEY AUTOINCREMENT",
    message_id: str = "TEXT NOT NULL UNIQUE",
    content: str = "TEXT NOT NULL",
    include_created_at: bool = True,
) -> str:
    created_at = ", created_at TEXT NOT NULL DEFAULT ''" if include_created_at else ""
    return f"""
    CREATE TABLE transcript (
        sequence {sequence},
        message_id {message_id},
        role TEXT NOT NULL,
        content {content},
        run_id TEXT NOT NULL DEFAULT '',
        turn_id TEXT NOT NULL DEFAULT '',
        call_id TEXT NOT NULL DEFAULT '',
        artifact_ref TEXT NOT NULL DEFAULT ''
        {created_at}
    )
    """


def _add_schema_objects(path: Path, statements: list[str]) -> None:
    with sqlite3.connect(path) as connection:
        for statement in statements:
            connection.execute(statement)


def test_path_append_and_rebuild_idempotency(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    item = TranscriptItem(message_id="m1", role="user", content="hello")

    assert store.path == tmp_path / "evidence" / "s1" / "transcript.sqlite3"
    assert store.append(item) is True
    assert store.append(item) is False

    rebuilt = TranscriptStore(tmp_path, "s1")
    assert rebuilt.append(item) is False
    assert rebuilt.read_all() == [item]


def test_unicode_metadata_and_sequence_order_round_trip(tmp_path):
    items = [
        TranscriptItem(
            message_id="m2",
            role="tool",
            content="你好，世界 🌍",
            run_id="run_1",
            turn_id="turn_1",
            call_id="call_1",
            artifact_ref="artifact.txt",
            created_at="2026-07-11T12:00:00Z",
        ),
        TranscriptItem(message_id="m1", role="assistant", content="second"),
    ]
    store = TranscriptStore(tmp_path, "session")

    for item in items:
        assert store.append(item) is True

    assert store.read_all() == items


def test_two_preconstructed_stores_in_threads_append_same_message_once(tmp_path):
    stores = [TranscriptStore(tmp_path, "s1"), TranscriptStore(tmp_path, "s1")]
    item = TranscriptItem(message_id="m1", role="user", content="hello")
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def append(store: TranscriptStore) -> None:
        barrier.wait()
        results.append(store.append(item))

    threads = [threading.Thread(target=append, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert sorted(results) == [False, True]
    assert stores[0].read_all() == [item]


def test_spawn_processes_append_same_message_once(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=_spawn_append, args=(str(tmp_path), ready, start, results))
        for _ in range(2)
    ]
    started_processes = []

    try:
        for process in processes:
            process.start()
            started_processes.append(process)
        for _ in processes:
            assert ready.get(timeout=10) is True
        start.set()
        for process in processes:
            process.join(timeout=10)

        assert [process.exitcode for process in processes] == [0, 0]
        try:
            appended = [results.get(timeout=2) for _ in processes]
        except Empty as exc:  # pragma: no cover - produces a clearer assertion failure
            raise AssertionError("spawn worker did not report a result") from exc
        assert sorted(appended) == [False, True]
        assert len(TranscriptStore(tmp_path, "s1").read_all()) == 1
    finally:
        start.set()
        for process in started_processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)


def test_schema_version_columns_and_connection_pragmas(tmp_path):
    store = TranscriptStore(tmp_path, "s1")

    assert store.schema_version == 1
    with store._connect() as connection:
        columns = connection.execute("PRAGMA table_info(transcript)").fetchall()
        create_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'transcript'"
        ).fetchone()[0]
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000

    assert [column[1] for column in columns] == [
        "sequence",
        "message_id",
        "role",
        "content",
        "run_id",
        "turn_id",
        "call_id",
        "artifact_ref",
        "created_at",
    ]
    assert columns[0][2:] == ("INTEGER", 0, None, 1)
    assert columns[1][2:] == ("TEXT", 1, None, 0)
    assert columns[2][2:] == ("TEXT", 1, None, 0)
    assert columns[3][2:] == ("TEXT", 1, None, 0)
    for column in columns[4:]:
        assert column[2:] == ("TEXT", 1, "''", 0)
    assert "PRIMARY KEY AUTOINCREMENT" in create_sql.upper()
    assert "MESSAGE_ID TEXT NOT NULL UNIQUE" in create_sql.upper()


def test_unknown_newer_schema_version_is_rejected_with_path(tmp_path):
    path = tmp_path / "evidence" / "s1" / "transcript.sqlite3"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=2")

    with pytest.raises(RuntimeError) as exc_info:
        TranscriptStore(tmp_path, "s1")

    assert str(path) in str(exc_info.value)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.parametrize(
    "schema_sql",
    [
        None,
        _transcript_schema(include_created_at=False),
        _transcript_schema(content="BLOB NOT NULL"),
        _transcript_schema(message_id="TEXT NOT NULL"),
        _transcript_schema(sequence="INTEGER NOT NULL"),
        _transcript_schema(sequence="INTEGER PRIMARY KEY"),
    ],
    ids=[
        "missing-table",
        "missing-column",
        "wrong-type",
        "missing-message-id-unique",
        "wrong-primary-key",
        "missing-autoincrement",
    ],
)
def test_v1_malformed_schema_is_rejected_immediately_with_path(tmp_path, schema_sql):
    path = _precreate_database(tmp_path, schema_sql, 1)

    with pytest.raises(TranscriptStoreError) as exc_info:
        TranscriptStore(tmp_path, "s1")

    assert str(path) in str(exc_info.value)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_v1_external_unique_index_does_not_replace_message_id_constraint(tmp_path):
    path = _precreate_database(
        tmp_path,
        _transcript_schema(message_id="TEXT NOT NULL"),
        1,
    )
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE UNIQUE INDEX external_unique ON transcript(message_id)")

    with pytest.raises(TranscriptStoreError) as exc_info:
        TranscriptStore(tmp_path, "s1")

    assert str(path) in str(exc_info.value)


@pytest.mark.parametrize(
    "schema_sql",
    [
        "CREATE TABLE unrelated (value TEXT)",
        "CREATE TABLE sqliteX (value TEXT)",
        _transcript_schema(include_created_at=False),
    ],
    ids=["unrelated-table", "sqlite-prefix-user-table", "malformed-transcript"],
)
def test_v0_database_with_any_user_table_is_rejected_without_upgrade(tmp_path, schema_sql):
    path = _precreate_database(tmp_path, schema_sql, 0)
    with sqlite3.connect(path) as connection:
        original_tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT GLOB 'sqlite_*'"
        ).fetchall()

    with pytest.raises(TranscriptStoreError) as exc_info:
        TranscriptStore(tmp_path, "s1")

    assert str(path) in str(exc_info.value)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT GLOB 'sqlite_*'"
            ).fetchall()
            == original_tables
        )


@pytest.mark.parametrize(
    "statements",
    [
        ["CREATE VIEW user_view AS SELECT 1 AS value"],
        [
            "CREATE TABLE sqliteX (value TEXT)",
            "CREATE TRIGGER user_trigger AFTER INSERT ON sqliteX BEGIN SELECT 1; END",
        ],
        [
            "CREATE TABLE sqliteX (value TEXT)",
            "CREATE INDEX user_index ON sqliteX(value)",
        ],
    ],
    ids=["view", "trigger", "index"],
)
def test_v0_database_with_any_user_schema_object_is_rejected(tmp_path, statements):
    path = _precreate_database(tmp_path, None, 0)
    _add_schema_objects(path, statements)

    with pytest.raises(TranscriptStoreError) as exc_info:
        TranscriptStore(tmp_path, "s1")

    assert str(path) in str(exc_info.value)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0


def test_truly_empty_v0_database_is_created_as_v1_and_reopens(tmp_path):
    path = _precreate_database(tmp_path, None, 0)

    store = TranscriptStore(tmp_path, "s1")
    reopened = TranscriptStore(tmp_path, "s1")

    assert store.read_all() == []
    assert reopened.read_all() == []
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE VIEW user_view AS SELECT message_id FROM transcript",
        "CREATE INDEX user_index ON transcript(role)",
        """
        CREATE TRIGGER delete_evidence AFTER INSERT ON transcript
        BEGIN
            DELETE FROM transcript WHERE message_id = NEW.message_id;
        END
        """,
    ],
    ids=["view", "index", "trigger-delete-evidence"],
)
def test_v1_canonical_table_with_any_extra_user_object_is_rejected(tmp_path, statement):
    store = TranscriptStore(tmp_path, "s1")
    _add_schema_objects(store.path, [statement])

    with pytest.raises(TranscriptStoreError) as exc_info:
        TranscriptStore(tmp_path, "s1")

    assert str(store.path) in str(exc_info.value)


def test_v1_check_constraint_cannot_spoof_autoincrement_schema(tmp_path):
    schema = _transcript_schema(sequence="INTEGER PRIMARY KEY").replace(
        "message_id TEXT NOT NULL UNIQUE,",
        "message_id TEXT NOT NULL UNIQUE CHECK "
        "('sequence INTEGER PRIMARY KEY AUTOINCREMENT' != ''),",
    )
    path = _precreate_database(tmp_path, schema, 1)

    with pytest.raises(TranscriptStoreError) as exc_info:
        TranscriptStore(tmp_path, "s1")

    assert str(path) in str(exc_info.value)


def test_schema_inspection_operational_error_includes_path_and_cause(tmp_path, monkeypatch):
    path = _precreate_database(tmp_path, None, 0)
    real_connect = sqlite3.connect

    class ConnectionProxy:
        def __init__(self, *args, **kwargs):
            self._connection = real_connect(*args, **kwargs)

        def execute(self, sql, parameters=()):
            if sql.strip() == "PRAGMA table_info(transcript)":
                raise sqlite3.OperationalError("schema inspection failed")
            return self._connection.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self._connection, name)

    monkeypatch.setattr(transcript_module.sqlite3, "connect", ConnectionProxy)

    with pytest.raises(TranscriptStoreError) as exc_info:
        TranscriptStore(tmp_path, "s1")

    assert str(path) in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)
    assert "schema inspection failed" in str(exc_info.value.__cause__)


def test_uncommitted_raw_transaction_is_not_visible_after_close(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    connection = sqlite3.connect(store.path)
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "INSERT INTO transcript(message_id, role, content) VALUES (?, ?, ?)",
        ("uncommitted", "user", "hidden"),
    )
    connection.close()

    assert TranscriptStore(tmp_path, "s1").read_all() == []


def test_uncommitted_raw_transaction_is_not_visible_after_process_exit(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    script = """
import os
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
connection.execute("BEGIN IMMEDIATE")
connection.execute(
    "INSERT INTO transcript(message_id, role, content) VALUES (?, ?, ?)",
    ("uncommitted", "user", "hidden"),
)
os._exit(0)
"""

    subprocess.run([sys.executable, "-c", script, str(store.path)], check=True)

    assert TranscriptStore(tmp_path, "s1").read_all() == []


def test_committed_raw_transaction_survives_reopen(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "INSERT INTO transcript(message_id, role, content) VALUES (?, ?, ?)",
            ("committed", "assistant", "visible"),
        )

    assert TranscriptStore(tmp_path, "s1").read_all() == [
        TranscriptItem(message_id="committed", role="assistant", content="visible")
    ]


def test_busy_writer_raises_operational_error_with_bounded_wait(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path, "s1")
    monkeypatch.setattr(transcript_module, "_BUSY_TIMEOUT_MS", 100)
    blocker = sqlite3.connect(store.path)
    blocker.execute("BEGIN IMMEDIATE")
    started = time.monotonic()

    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.append(TranscriptItem(message_id="m1", role="user", content="hello"))
    finally:
        elapsed = time.monotonic() - started
        blocker.rollback()
        blocker.close()

    assert elapsed >= 0.08
    assert elapsed < 3.0


def test_integrity_check_and_corruption_error_include_database_path(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    assert store.check_integrity() is True
    original_mode = _mode(store.path)
    corrupt = b"not a sqlite database"
    store.path.write_bytes(corrupt)

    with pytest.raises(TranscriptCorruptionError) as exc_info:
        store.check_integrity()

    assert str(store.path) in str(exc_info.value)
    assert store.path.read_bytes() == corrupt
    assert _mode(store.path) == original_mode


def test_constructor_rejects_corrupt_database_without_replacing_it(tmp_path):
    path = tmp_path / "evidence" / "s1" / "transcript.sqlite3"
    path.parent.mkdir(parents=True)
    corrupt = b"not a sqlite database"
    path.write_bytes(corrupt)

    with pytest.raises(TranscriptCorruptionError) as exc_info:
        TranscriptStore(tmp_path, "s1")

    assert str(path) in str(exc_info.value)
    assert path.read_bytes() == corrupt


def test_export_jsonl_is_stable_unicode_private_and_atomically_replaced(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    items = [
        TranscriptItem(message_id="m2", role="user", content="你好"),
        TranscriptItem(message_id="m1", role="assistant", content="second"),
    ]
    for item in items:
        store.append(item)

    path = store.export_jsonl()
    original_inode = path.stat().st_ino
    first_export = path.read_text(encoding="utf-8")
    exported = [TranscriptItem(**json.loads(line)) for line in first_export.splitlines()]

    assert path == tmp_path / "evidence" / "s1" / "exports" / "transcript.jsonl"
    assert exported == items
    assert "你好" in first_export
    assert "\\u4f60" not in first_export
    assert _mode(path) == 0o600

    assert store.export_jsonl() == path
    assert path.read_text(encoding="utf-8") == first_export
    assert path.stat().st_ino != original_inode


@pytest.mark.parametrize("failure", ["write", "replace"])
def test_failed_export_preserves_database_and_previous_export(tmp_path, monkeypatch, failure):
    store = TranscriptStore(tmp_path, "s1")
    first = TranscriptItem(message_id="m1", role="user", content="first")
    second = TranscriptItem(message_id="m2", role="assistant", content="second")
    store.append(first)
    export_path = store.export_jsonl()
    original_export = export_path.read_bytes()
    original_inode = export_path.stat().st_ino
    store.append(second)

    if failure == "write":

        def fail_write(fd: int, data: bytes) -> None:
            raise OSError("write failed")

        monkeypatch.setattr(atomic_export_module, "_write_all", fail_write)
        error = "write failed"
    else:

        def fail_replace(*args, **kwargs) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(atomic_export_module.os, "replace", fail_replace)
        error = "replace failed"

    with pytest.raises(OSError, match=error):
        store.export_jsonl()

    assert store.read_all() == [first, second]
    assert export_path.read_bytes() == original_export
    assert export_path.stat().st_ino == original_inode
    assert set(export_path.parent.iterdir()) == {
        export_path,
        export_path.parent / ".export.lock",
    }


def test_post_replace_directory_sync_failure_restores_previous_export(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path, "s1")
    first = TranscriptItem(message_id="m1", role="user", content="first")
    second = TranscriptItem(message_id="m2", role="assistant", content="second")
    store.append(first)
    export_path = store.export_jsonl()
    previous_export = export_path.read_bytes()
    store.append(second)
    real_fsync = os.fsync
    failed = False

    def fail_after_publish(fd: int) -> None:
        nonlocal failed
        if (
            not failed
            and stat.S_ISDIR(os.fstat(fd).st_mode)
            and export_path.read_bytes() != previous_export
        ):
            failed = True
            raise OSError("publish directory sync failed")
        real_fsync(fd)

    monkeypatch.setattr(atomic_export_module.os, "fsync", fail_after_publish)

    with pytest.raises(OSError, match="publish directory sync failed"):
        store.export_jsonl()

    assert failed is True
    assert export_path.read_bytes() == previous_export
    assert store.read_all() == [first, second]
    assert set(export_path.parent.iterdir()) == {
        export_path,
        export_path.parent / ".export.lock",
    }


def test_post_replace_directory_sync_failure_removes_new_export(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path, "s1")
    item = TranscriptItem(message_id="m1", role="user", content="first")
    store.append(item)
    export_path = store.path.parent / "exports" / "transcript.jsonl"
    export_path.parent.mkdir(mode=0o700)
    real_fsync = os.fsync
    failed = False

    def fail_after_publish(fd: int) -> None:
        nonlocal failed
        if not failed and stat.S_ISDIR(os.fstat(fd).st_mode) and export_path.exists():
            failed = True
            raise OSError("publish directory sync failed")
        real_fsync(fd)

    monkeypatch.setattr(atomic_export_module.os, "fsync", fail_after_publish)

    with pytest.raises(OSError, match="publish directory sync failed"):
        store.export_jsonl()

    assert failed is True
    assert not export_path.exists()
    assert store.read_all() == [item]
    assert list(export_path.parent.iterdir()) == [export_path.parent / ".export.lock"]


def test_export_reports_rollback_sync_failure_with_publish_error_as_cause(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="user", content="first"))
    export_path = store.export_jsonl()
    previous_export = export_path.read_bytes()
    store.append(TranscriptItem(message_id="m2", role="assistant", content="second"))
    real_fsync = os.fsync
    publish_failed = False

    def fail_publish_and_rollback_sync(fd: int) -> None:
        nonlocal publish_failed
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            if not publish_failed and export_path.read_bytes() != previous_export:
                publish_failed = True
                raise OSError("publish directory sync failed")
            if publish_failed and export_path.read_bytes() == previous_export:
                raise OSError("rollback directory sync failed")
        real_fsync(fd)

    monkeypatch.setattr(atomic_export_module.os, "fsync", fail_publish_and_rollback_sync)

    with pytest.raises(OSError, match="rollback directory sync failed") as exc_info:
        store.export_jsonl()

    assert isinstance(exc_info.value.__cause__, OSError)
    assert "publish directory sync failed" in str(exc_info.value.__cause__)
    assert export_path.read_bytes() == previous_export
    backups = list(export_path.parent.glob(".transcript.jsonl.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == previous_export
    assert _mode(backups[0]) == 0o600


def test_backup_cleanup_sync_failure_after_commit_returns_new_export(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="user", content="first"))
    export_path = store.export_jsonl()
    previous_export = export_path.read_bytes()
    store.append(TranscriptItem(message_id="m2", role="assistant", content="second"))
    real_fsync = os.fsync
    directory_syncs_after_publish = 0

    def fail_backup_cleanup_sync(fd: int) -> None:
        nonlocal directory_syncs_after_publish
        if stat.S_ISDIR(os.fstat(fd).st_mode) and export_path.read_bytes() != previous_export:
            directory_syncs_after_publish += 1
            if directory_syncs_after_publish == 2:
                raise OSError("backup cleanup sync failed")
        real_fsync(fd)

    monkeypatch.setattr(atomic_export_module.os, "fsync", fail_backup_cleanup_sync)

    assert store.export_jsonl() == export_path

    assert directory_syncs_after_publish == 2
    assert export_path.read_bytes() != previous_export
    assert b'"message_id": "m2"' in export_path.read_bytes()
    assert [item.message_id for item in store.read_all()] == ["m1", "m2"]


def test_backup_unlink_failure_after_commit_returns_new_export_and_keeps_backup(
    tmp_path, monkeypatch
):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="user", content="first"))
    export_path = store.export_jsonl()
    previous_export = export_path.read_bytes()
    store.append(TranscriptItem(message_id="m2", role="assistant", content="second"))
    real_unlink = os.unlink

    def fail_backup_unlink(path, *, dir_fd=None):
        if str(path).endswith(".bak"):
            raise OSError("backup unlink failed")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(atomic_export_module.os, "unlink", fail_backup_unlink)

    assert store.export_jsonl() == export_path

    assert export_path.read_bytes() != previous_export
    assert b'"message_id": "m2"' in export_path.read_bytes()
    backups = list(export_path.parent.glob(".transcript.jsonl.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == previous_export
    assert [item.message_id for item in store.read_all()] == ["m1", "m2"]


def test_later_successful_export_preserves_prior_recovery_backup(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="user", content="first"))
    export_path = store.export_jsonl()
    store.append(TranscriptItem(message_id="m2", role="assistant", content="second"))
    real_unlink = os.unlink

    with monkeypatch.context() as context:

        def fail_backup_unlink(path, *, dir_fd=None):
            if str(path).endswith(".bak"):
                raise OSError("backup unlink failed")
            real_unlink(path, dir_fd=dir_fd)

        context.setattr(atomic_export_module.os, "unlink", fail_backup_unlink)
        assert store.export_jsonl() == export_path

    backups = list(export_path.parent.glob(".transcript.jsonl.*.bak"))
    assert len(backups) == 1
    recovery_path = backups[0]
    recovery_payload = recovery_path.read_bytes()

    store.append(TranscriptItem(message_id="m3", role="assistant", content="third"))
    assert store.export_jsonl() == export_path

    assert recovery_path.read_bytes() == recovery_payload
    assert [item.message_id for item in store.read_all()] == ["m1", "m2", "m3"]


def test_successful_export_never_scans_historical_backups(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="user", content="first"))
    export_path = store.export_jsonl()
    previous_export = export_path.read_bytes()
    store.append(TranscriptItem(message_id="m2", role="assistant", content="second"))

    def forbid_listdir(path):
        raise AssertionError("historical backup scan attempted")

    monkeypatch.setattr(atomic_export_module.os, "listdir", forbid_listdir)

    assert store.export_jsonl() == export_path

    assert export_path.read_bytes() != previous_export
    assert b'"message_id": "m2"' in export_path.read_bytes()
    assert store.read_all() == [
        TranscriptItem(message_id="m1", role="user", content="first"),
        TranscriptItem(message_id="m2", role="assistant", content="second"),
    ]


def test_unrelated_backup_like_file_is_never_deleted(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="user", content="first"))
    export_path = store.export_jsonl()
    notes = export_path.parent / ".transcript.jsonl.notes.bak"
    notes.write_bytes(b"operator notes")
    notes.chmod(0o640)

    assert store.export_jsonl() == export_path

    assert notes.read_bytes() == b"operator notes"
    assert _mode(notes) == 0o640


def test_second_export_blocks_while_first_has_active_durable_backup(tmp_path, monkeypatch):
    stores = [TranscriptStore(tmp_path, "s1"), TranscriptStore(tmp_path, "s1")]
    stores[0].append(TranscriptItem(message_id="m1", role="user", content="first"))
    stores[0].export_jsonl()
    stores[0].append(TranscriptItem(message_id="m2", role="assistant", content="second"))
    backup_durable = threading.Event()
    release_first = threading.Event()
    second_started = threading.Event()
    first_result: list[Path] = []
    second_result: list[Path] = []
    failures: list[BaseException] = []
    real_fsync = os.fsync
    export_directory = stores[0].path.parent / "exports"

    def pause_after_backup_fsync(fd: int) -> None:
        real_fsync(fd)
        metadata = os.fstat(fd)
        if (
            stat.S_ISDIR(metadata.st_mode)
            and any(
                re.fullmatch(r"\.transcript\.jsonl\.[0-9a-f]{32}\.bak", path.name)
                for path in export_directory.iterdir()
            )
            and not backup_durable.is_set()
        ):
            backup_durable.set()
            if not release_first.wait(10):
                raise RuntimeError("first exporter release timed out")

    monkeypatch.setattr(atomic_export_module.os, "fsync", pause_after_backup_fsync)

    def export_first() -> None:
        try:
            first_result.append(stores[0].export_jsonl())
        except BaseException as exc:  # pragma: no cover - asserted in parent thread
            failures.append(exc)

    def export_second() -> None:
        second_started.set()
        try:
            second_result.append(stores[1].export_jsonl())
        except BaseException as exc:  # pragma: no cover - asserted in parent thread
            failures.append(exc)

    first = threading.Thread(target=export_first)
    second = threading.Thread(target=export_second)
    first.start()
    assert backup_durable.wait(10)
    second.start()
    assert second_started.wait(2)
    second.join(timeout=0.2)

    try:
        assert second.is_alive(), "second exporter did not block on the session export lock"
    finally:
        release_first.set()
        first.join(timeout=10)
        second.join(timeout=10)

    assert failures == []
    assert first_result == second_result == [
        tmp_path / "evidence" / "s1" / "exports" / "transcript.jsonl"
    ]


def test_export_lock_covers_snapshot_so_older_export_cannot_overwrite_newer_snapshot(
    tmp_path, monkeypatch
):
    stores = [TranscriptStore(tmp_path, "s1"), TranscriptStore(tmp_path, "s1")]
    first = TranscriptItem(message_id="m1", role="user", content="first")
    second = TranscriptItem(message_id="m2", role="assistant", content="second")
    stores[0].append(first)
    old_snapshot_encoded = threading.Event()
    release_old_export = threading.Event()
    new_export_started = threading.Event()
    failures: list[BaseException] = []
    real_dumps = json.dumps

    def pause_old_snapshot(value, *args, **kwargs):
        if threading.current_thread().name == "old-snapshot-export":
            old_snapshot_encoded.set()
            if not release_old_export.wait(10):
                raise RuntimeError("old snapshot release timed out")
        return real_dumps(value, *args, **kwargs)

    monkeypatch.setattr(transcript_module.json, "dumps", pause_old_snapshot)

    def export_old_snapshot() -> None:
        try:
            stores[0].export_jsonl()
        except BaseException as exc:  # pragma: no cover - asserted in parent thread
            failures.append(exc)

    def export_new_snapshot() -> None:
        new_export_started.set()
        try:
            stores[1].export_jsonl()
        except BaseException as exc:  # pragma: no cover - asserted in parent thread
            failures.append(exc)

    old_export = threading.Thread(target=export_old_snapshot, name="old-snapshot-export")
    new_export = threading.Thread(target=export_new_snapshot, name="new-snapshot-export")
    old_export.start()
    assert old_snapshot_encoded.wait(10)
    stores[1].append(second)
    new_export.start()
    assert new_export_started.wait(2)
    new_export.join(timeout=0.2)
    new_export_was_blocked = new_export.is_alive()

    try:
        release_old_export.set()
        old_export.join(timeout=10)
        new_export.join(timeout=10)
    finally:
        release_old_export.set()

    assert new_export_was_blocked, "new exporter read its snapshot before acquiring the lock"
    assert not old_export.is_alive()
    assert not new_export.is_alive()
    assert failures == []
    export_path = stores[0].path.parent / "exports" / "transcript.jsonl"
    assert [
        TranscriptItem(**json.loads(line)) for line in export_path.read_text().splitlines()
    ] == [first, second]


def test_threaded_exporters_publish_complete_snapshot_without_recovery_loss(tmp_path):
    stores = [TranscriptStore(tmp_path, "s1"), TranscriptStore(tmp_path, "s1")]
    items = [
        TranscriptItem(message_id="m1", role="user", content="first"),
        TranscriptItem(message_id="m2", role="assistant", content="second"),
    ]
    for item in items:
        stores[0].append(item)
    stores[0].export_jsonl()
    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def export(store: TranscriptStore) -> None:
        try:
            barrier.wait()
            store.export_jsonl()
        except BaseException as exc:  # pragma: no cover - asserted in parent thread
            failures.append(exc)

    threads = [threading.Thread(target=export, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    export_path = stores[0].path.parent / "exports" / "transcript.jsonl"
    assert [TranscriptItem(**json.loads(line)) for line in export_path.read_text().splitlines()] == items
    assert list(export_path.parent.glob(".transcript.jsonl.*.bak")) == []


def test_spawn_exporters_publish_complete_snapshot(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    items = [
        TranscriptItem(message_id="m1", role="user", content="first"),
        TranscriptItem(message_id="m2", role="assistant", content="second"),
    ]
    for item in items:
        store.append(item)
    store.export_jsonl()
    context = multiprocessing.get_context("spawn")
    ready = context.Queue()
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=_spawn_export, args=(str(tmp_path), ready, start, results))
        for _ in range(2)
    ]
    started_processes = []

    try:
        for process in processes:
            process.start()
            started_processes.append(process)
        for _ in processes:
            assert ready.get(timeout=10) is True
        start.set()
        for process in processes:
            process.join(timeout=10)

        assert [process.exitcode for process in processes] == [0, 0]
        payloads = [results.get(timeout=2) for _ in processes]
        assert all(isinstance(payload, bytes) for payload in payloads)
        expected = store.export_jsonl().read_bytes()
        assert payloads == [expected, expected]
        assert list((store.path.parent / "exports").glob(".transcript.jsonl.*.bak")) == []
    finally:
        start.set()
        for process in started_processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)


def test_export_lock_is_private_and_rejects_links(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="user", content="first"))
    export_path = store.export_jsonl()
    lock_path = export_path.parent / ".export.lock"

    assert _mode(lock_path) == 0o600

    lock_path.unlink()
    outside = tmp_path / "outside.lock"
    outside.write_bytes(b"outside")
    outside.chmod(0o640)
    lock_path.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        store.export_jsonl()

    assert outside.read_bytes() == b"outside"
    assert _mode(outside) == 0o640


def test_operator_owned_matching_backup_name_is_never_deleted(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="user", content="first"))
    export_path = store.export_jsonl()
    operator_backup = export_path.parent / f".transcript.jsonl.{'a' * 32}.bak"
    unrelated = [
        export_path.parent / ".transcript.jsonl.notes.bak",
        export_path.parent / f".transcript.jsonl.{'D' * 32}.bak",
        export_path.parent / f".transcript.jsonl.{'b' * 31}.bak",
    ]
    operator_backup.write_bytes(b"operator recovery evidence")
    for path in unrelated:
        path.write_bytes(path.name.encode())

    store.export_jsonl()

    assert operator_backup.read_bytes() == b"operator recovery evidence"
    assert all(path.exists() for path in unrelated)


def test_export_rejects_symlink_target_before_external_change(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    item = TranscriptItem(message_id="m1", role="user", content="first")
    store.append(item)
    outside = tmp_path / "outside.jsonl"
    original = b"outside"
    outside.write_bytes(original)
    outside.chmod(0o640)
    export_path = store.path.parent / "exports" / "transcript.jsonl"
    export_path.parent.mkdir(mode=0o700)
    export_path.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        store.export_jsonl()

    assert export_path.is_symlink()
    assert outside.read_bytes() == original
    assert _mode(outside) == 0o640
    assert store.read_all() == [item]


def test_export_rejects_hardlink_target_before_external_change(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    item = TranscriptItem(message_id="m1", role="user", content="first")
    store.append(item)
    outside = tmp_path / "outside.jsonl"
    original = b"outside"
    outside.write_bytes(original)
    outside.chmod(0o640)
    export_path = store.path.parent / "exports" / "transcript.jsonl"
    export_path.parent.mkdir(mode=0o700)
    os.link(outside, export_path)

    with pytest.raises(ValueError, match="hardlink"):
        store.export_jsonl()

    assert export_path.samefile(outside)
    assert outside.read_bytes() == original
    assert _mode(outside) == 0o640
    assert store.read_all() == [item]


def test_export_unique_temp_and_backup_names_ignore_precreated_symlinks(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="user", content="first"))
    export_path = store.export_jsonl()
    store.append(TranscriptItem(message_id="m2", role="assistant", content="second"))
    outside = tmp_path / "outside"
    original = b"outside"
    outside.write_bytes(original)
    outside.chmod(0o640)
    taken = "a" * 32
    temp_ok = "b" * 32
    backup_ok = "c" * 32
    temp_link = export_path.parent / f".transcript.jsonl.{taken}.tmp"
    backup_link = export_path.parent / f".transcript.jsonl.{taken}.bak"
    temp_link.symlink_to(outside)
    backup_link.symlink_to(outside)
    tokens = [taken, temp_ok, taken, backup_ok]

    def next_token(size: int) -> str:
        assert size == 16
        return tokens.pop(0)

    monkeypatch.setattr(atomic_export_module.secrets, "token_hex", next_token)

    assert store.export_jsonl() == export_path

    assert tokens == []
    assert temp_link.is_symlink()
    assert backup_link.is_symlink()
    assert outside.read_bytes() == original
    assert _mode(outside) == 0o640
    assert [item.message_id for item in store.read_all()] == ["m1", "m2"]


def test_legacy_jsonl_is_not_imported_or_overwritten(tmp_path):
    legacy = tmp_path / "evidence" / "s1" / "transcript.jsonl"
    legacy.parent.mkdir(parents=True)
    original = b'{"message_id":"legacy"}\n'
    legacy.write_bytes(original)

    store = TranscriptStore(tmp_path, "s1")

    assert store.read_all() == []
    assert legacy.read_bytes() == original


@pytest.mark.parametrize("session_id", ["", ".", "..", "nested/session", r"nested\session"])
def test_invalid_session_ids_are_rejected(tmp_path, session_id):
    with pytest.raises(ValueError):
        TranscriptStore(tmp_path, session_id)


def test_symlinked_root_is_rejected_without_outside_changes(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "trusted"
    root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        TranscriptStore(root, "s1")

    assert list(outside.iterdir()) == []


def test_new_trusted_root_is_private_under_default_umask(tmp_path):
    root = tmp_path / "trusted"
    previous_umask = os.umask(0o022)
    try:
        TranscriptStore(root, "s1")
    finally:
        os.umask(previous_umask)

    assert _mode(root) == 0o700


def test_existing_owned_trusted_root_is_tightened_to_private_mode(tmp_path):
    root = tmp_path / "trusted"
    root.mkdir(mode=0o755)
    root.chmod(0o755)

    TranscriptStore(root, "s1")

    assert _mode(root) == 0o700


def test_symlinked_session_is_rejected_without_outside_changes(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    session = tmp_path / "evidence" / "s1"
    session.parent.mkdir()
    session.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        TranscriptStore(tmp_path, "s1")

    assert list(outside.iterdir()) == []


def test_symlinked_database_is_rejected_without_outside_changes(tmp_path):
    outside = tmp_path / "outside.sqlite3"
    original = b"outside"
    outside.write_bytes(original)
    path = tmp_path / "evidence" / "s1" / "transcript.sqlite3"
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        TranscriptStore(tmp_path, "s1")

    assert outside.read_bytes() == original


def test_hardlinked_database_is_rejected_before_chmod_or_write(tmp_path):
    root = tmp_path / "trusted"
    root.mkdir()
    outside = tmp_path / "outside.sqlite3"
    original = b"outside"
    outside.write_bytes(original)
    outside.chmod(0o640)
    path = root / "evidence" / "s1" / "transcript.sqlite3"
    path.parent.mkdir(parents=True)
    os.link(outside, path)

    with pytest.raises(ValueError, match="hardlink"):
        TranscriptStore(root, "s1")

    assert outside.read_bytes() == original
    assert _mode(outside) == 0o640


def test_directory_permission_failure_does_not_leak_file_descriptors(tmp_path, monkeypatch):
    real_fchmod = os.fchmod

    def fail_directory_chmod(fd: int, mode: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("directory chmod failed")
        real_fchmod(fd, mode)

    monkeypatch.setattr(transcript_module.os, "fchmod", fail_directory_chmod)
    before = _fd_count()

    for index in range(10):
        with pytest.raises(OSError, match="directory chmod failed"):
            TranscriptStore(tmp_path / str(index), "s1")

    assert _fd_count() == before


def test_database_sidecars_export_and_directories_are_private(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    session = store.path.parent
    session.chmod(0o755)
    store.path.chmod(0o644)
    keeper = sqlite3.connect(store.path)
    keeper.execute("PRAGMA journal_mode=WAL")
    keeper.execute(
        "INSERT INTO transcript(message_id, role, content) VALUES (?, ?, ?)",
        ("m1", "user", "hello"),
    )
    keeper.commit()
    sidecars = [Path(f"{store.path}-wal"), Path(f"{store.path}-shm")]
    assert all(path.exists() for path in sidecars)
    for path in sidecars:
        path.chmod(0o644)

    try:
        store.read_all()
        export_path = store.export_jsonl()
        export_path.chmod(0o644)
        store.export_jsonl()

        assert _mode(store.path) == 0o600
        assert all(_mode(path) == 0o600 for path in sidecars)
        assert _mode(export_path) == 0o600
        assert _mode(session) == 0o700
        assert _mode(export_path.parent) == 0o700
    finally:
        keeper.close()


def test_quoted_sql_text_is_bound_as_data_and_round_trips(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    item = TranscriptItem(
        message_id="m'1",
        role="user",
        content="x'); DROP TABLE transcript; --",
        run_id="run'quoted",
    )

    assert store.append(item) is True
    assert store.read_all() == [item]
    assert store.append(TranscriptItem(message_id="m2", role="user", content="still here"))
    assert len(store.read_all()) == 2
