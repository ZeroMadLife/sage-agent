from __future__ import annotations

import json
import multiprocessing
import os
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty

import pytest

from core.coding.persistence import transcript_store as transcript_module
from core.coding.persistence.transcript_store import (
    TranscriptCorruptionError,
    TranscriptItem,
    TranscriptStore,
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


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _fd_count() -> int:
    return len(os.listdir("/dev/fd"))


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

    for process in processes:
        process.start()
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
    assert elapsed < 1.5


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

        monkeypatch.setattr(transcript_module, "_write_all", fail_write)
        error = "write failed"
    else:

        def fail_replace(*args, **kwargs) -> None:
            raise OSError("replace failed")

        monkeypatch.setattr(transcript_module.os, "replace", fail_replace)
        error = "replace failed"

    with pytest.raises(OSError, match=error):
        store.export_jsonl()

    assert store.read_all() == [first, second]
    assert export_path.read_bytes() == original_export
    assert export_path.stat().st_ino == original_inode
    assert list(export_path.parent.iterdir()) == [export_path]


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

    monkeypatch.setattr(transcript_module.os, "fsync", fail_after_publish)

    with pytest.raises(OSError, match="publish directory sync failed"):
        store.export_jsonl()

    assert failed is True
    assert export_path.read_bytes() == previous_export
    assert store.read_all() == [first, second]
    assert list(export_path.parent.iterdir()) == [export_path]


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

    monkeypatch.setattr(transcript_module.os, "fsync", fail_after_publish)

    with pytest.raises(OSError, match="publish directory sync failed"):
        store.export_jsonl()

    assert failed is True
    assert not export_path.exists()
    assert store.read_all() == [item]
    assert list(export_path.parent.iterdir()) == []


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

    monkeypatch.setattr(transcript_module.os, "fsync", fail_publish_and_rollback_sync)

    with pytest.raises(OSError, match="rollback directory sync failed") as exc_info:
        store.export_jsonl()

    assert isinstance(exc_info.value.__cause__, OSError)
    assert "publish directory sync failed" in str(exc_info.value.__cause__)
    assert export_path.read_bytes() == previous_export


def test_backup_cleanup_sync_failure_restores_previous_export(tmp_path, monkeypatch):
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

    monkeypatch.setattr(transcript_module.os, "fsync", fail_backup_cleanup_sync)

    with pytest.raises(OSError, match="backup cleanup sync failed"):
        store.export_jsonl()

    assert directory_syncs_after_publish == 2
    assert export_path.read_bytes() == previous_export
    assert list(export_path.parent.iterdir()) == [export_path]


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
    temp_link = export_path.parent / ".transcript.jsonl.taken.tmp"
    backup_link = export_path.parent / ".transcript.jsonl.taken.bak"
    temp_link.symlink_to(outside)
    backup_link.symlink_to(outside)
    tokens = ["taken", "temp-ok", "taken", "backup-ok"]

    def next_token(size: int) -> str:
        assert size == 8
        return tokens.pop(0)

    monkeypatch.setattr(transcript_module.secrets, "token_hex", next_token)

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
