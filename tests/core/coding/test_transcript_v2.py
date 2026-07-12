from __future__ import annotations

import json
import multiprocessing
import sqlite3
from copy import deepcopy
from pathlib import Path
from queue import Empty

import pytest

from core.coding.persistence.transcript_store import (
    MAX_ARGS_BYTES,
    MAX_ARGS_DEPTH,
    MAX_ARGS_ITEMS,
    TranscriptConflictError,
    TranscriptCorruptionError,
    TranscriptItem,
    TranscriptStore,
    TranscriptStoreError,
)

V1_SCHEMA = """
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


def _v1_database(root: Path) -> Path:
    path = root / "evidence" / "s1" / "transcript.sqlite3"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute(V1_SCHEMA)
        connection.execute("PRAGMA user_version=1")
    return path


def _spawn_distinct_append(root: str, index: int, start, results) -> None:
    store = TranscriptStore(Path(root), "s1")
    if not start.wait(10):
        results.put(RuntimeError("start timed out"))
        return
    try:
        results.put(
            store.append_and_get_sequence(
                TranscriptItem(
                    message_id=f"m{index}",
                    role="tool",
                    content=f"result {index}",
                    name="read_file",
                    args={"index": index},
                )
            )
        )
    except Exception as exc:  # pragma: no cover - asserted in parent
        results.put(exc)


def test_fresh_database_uses_exact_v2_schema(tmp_path):
    store = TranscriptStore(tmp_path, "s1")

    assert store.schema_version == 2
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        columns = connection.execute("PRAGMA table_info(transcript)").fetchall()

    assert [column[1] for column in columns][-5:] == [
        "name",
        "args_json",
        "is_error",
        "policy_reason",
        "security_event_type",
    ]
    assert columns[-4][2:] == ("TEXT", 1, "'{}'", 0)
    assert columns[-3][2:] == ("INTEGER", 1, "0", 0)


def test_v1_migration_preserves_rows_gaps_and_autoincrement(tmp_path):
    path = _v1_database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO transcript(sequence, message_id, role, content) VALUES (2, 'm2', 'user', 'two')"
        )
        connection.execute(
            "INSERT INTO transcript(sequence, message_id, role, content) VALUES (7, 'm7', 'assistant', 'seven')"
        )

    store = TranscriptStore(tmp_path, "s1")

    assert [(item.sequence, item.message_id) for item in store.read_all()] == [(2, "m2"), (7, "m7")]
    inserted, sequence = store.append_and_get_sequence(
        TranscriptItem(message_id="m8", role="user", content="eight")
    )
    assert (inserted, sequence) == (True, 8)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='transcript'"
        ).fetchone() == (8,)


@pytest.mark.parametrize(
    "malicious",
    [
        "CREATE VIEW steal AS SELECT content FROM transcript",
        "CREATE TRIGGER erase AFTER INSERT ON transcript BEGIN DELETE FROM transcript; END",
        "CREATE INDEX extra_index ON transcript(role)",
    ],
)
def test_malicious_v1_objects_are_rejected_without_migration(tmp_path, malicious):
    path = _v1_database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute(malicious)

    with pytest.raises(TranscriptStoreError):
        TranscriptStore(tmp_path, "s1")

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert [row[1] for row in connection.execute("PRAGMA table_info(transcript)")] == [
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


def test_failed_v1_migration_rolls_back_every_schema_step(tmp_path, monkeypatch):
    path = _v1_database(tmp_path)
    from core.coding.persistence import transcript_schema

    real_add = transcript_schema._add_v2_column
    attempts = 0

    def fail_during_migration(connection, definition):
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            raise sqlite3.OperationalError("injected migration failure")
        return real_add(connection, definition)

    monkeypatch.setattr(transcript_schema, "_add_v2_column", fail_during_migration)

    with pytest.raises(TranscriptStoreError, match="injected migration failure"):
        TranscriptStore(tmp_path, "s1")

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert len(connection.execute("PRAGMA table_info(transcript)").fetchall()) == 9


def test_structured_fields_round_trip_and_args_are_stored_canonically(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    item = TranscriptItem(
        message_id="tool-1",
        role="tool",
        content="denied",
        name="shell",
        args={"z": [2, 1], "a": {"ok": True}},
        is_error=True,
        policy_reason="outside workspace",
        security_event_type="path_escape",
    )

    assert store.append_and_get_sequence(item) == (True, 1)
    read = store.read_all()[0]
    assert read.sequence == 1
    assert read.args == item.args
    assert read.name == "shell"
    assert read.is_error is True
    assert read.policy_reason == "outside workspace"
    assert read.security_event_type == "path_escape"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT args_json FROM transcript").fetchone()[0] == (
            '{"a":{"ok":true},"z":[2,1]}'
        )


@pytest.mark.parametrize("args", [[1], "value", 3, None])
def test_append_rejects_non_object_args(tmp_path, args):
    store = TranscriptStore(tmp_path, "s1")

    with pytest.raises(TypeError, match="dict"):
        store.append(TranscriptItem(message_id="m1", role="tool", content="x", args=args))


def test_append_rejects_non_json_safe_args(tmp_path):
    store = TranscriptStore(tmp_path, "s1")

    with pytest.raises((TypeError, ValueError)):
        store.append(
            TranscriptItem(message_id="m1", role="tool", content="x", args={"bad": object()})
        )


def test_duplicate_returns_original_sequence_but_conflicting_payload_raises(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    item = TranscriptItem(
        message_id="m1", role="tool", content="ok", name="read", args={"path": "a.py"}
    )

    assert store.append_and_get_sequence(item) == (True, 1)
    assert store.append_and_get_sequence(item) == (False, 1)
    with pytest.raises(TranscriptConflictError) as exc_info:
        store.append_and_get_sequence(
            TranscriptItem(
                message_id="m1",
                role="tool",
                content="changed",
                name="read",
                args={"path": "a.py"},
            )
        )

    assert "m1" in str(exc_info.value)
    assert str(store.path) in str(exc_info.value)

    with pytest.raises(TranscriptConflictError):
        store.append_and_get_sequence(
            TranscriptItem(
                message_id="m1",
                role="tool",
                content="ok",
                name="read",
                args={"path": "a.py"},
                policy_reason="new metadata",
            )
        )


def test_append_many_rolls_back_entire_legacy_backfill_on_conflict(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="existing", role="user", content="old"))

    with pytest.raises(TranscriptConflictError):
        store.append_many(
            [
                TranscriptItem(message_id="new", role="user", content="new"),
                TranscriptItem(message_id="existing", role="user", content="changed"),
            ]
        )

    assert [item.message_id for item in store.read_all()] == ["existing"]


def test_duplicate_compares_canonical_args_not_dict_insertion_order(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    first = TranscriptItem(message_id="m1", role="tool", content="ok", args={"a": 1, "b": 2})
    reordered = TranscriptItem(message_id="m1", role="tool", content="ok", args={"b": 2, "a": 1})

    assert store.append_and_get_sequence(first) == (True, 1)
    assert store.append_and_get_sequence(reordered) == (False, 1)


def test_spawned_distinct_appends_get_unique_stable_sequences(tmp_path):
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(target=_spawn_distinct_append, args=(str(tmp_path), index, start, results))
        for index in range(4)
    ]
    try:
        for process in processes:
            process.start()
        start.set()
        for process in processes:
            process.join(timeout=15)
        assert [process.exitcode for process in processes] == [0, 0, 0, 0]
        try:
            values = [results.get(timeout=2) for _ in processes]
        except Empty as exc:  # pragma: no cover
            raise AssertionError("worker did not report") from exc
        assert all(value[0] is True for value in values)
        assert sorted(value[1] for value in values) == [1, 2, 3, 4]
        assert [item.sequence for item in TranscriptStore(tmp_path, "s1").read_all()] == [
            1,
            2,
            3,
            4,
        ]
    finally:
        start.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)


def test_read_range_is_inclusive_ordered_and_validated(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    for index in range(1, 6):
        store.append(TranscriptItem(message_id=f"m{index}", role="user", content=str(index)))

    assert [item.sequence for item in store.read_range(2, 4)] == [2, 3, 4]
    assert store.read_range(6, 8) == []
    with pytest.raises(ValueError, match="start"):
        store.read_range(0, 1)
    with pytest.raises(ValueError, match="end"):
        store.read_range(3, 2)


def test_corrupt_args_json_raises_path_rich_error(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="tool", content="ok"))
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE transcript SET args_json='[]' WHERE message_id='m1'")

    with pytest.raises(TranscriptCorruptionError) as exc_info:
        store.read_all()

    assert str(store.path) in str(exc_info.value)
    assert "args_json" in str(exc_info.value)
    assert "m1" in str(exc_info.value)


def test_non_finite_args_json_is_corrupt(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="tool", content="ok"))
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE transcript SET args_json='{\"score\":NaN}'")

    with pytest.raises(TranscriptCorruptionError, match="args_json"):
        store.read_all()


def test_legacy_positional_arguments_keep_their_v1_meaning():
    item = TranscriptItem("m1", "tool", "ok", "run", "turn", "call", "artifact", "now")

    assert item.run_id == "run"
    assert item.created_at == "now"
    assert item.sequence == 0
    assert item.args == {}


def test_jsonl_export_contains_sequence_and_typed_fields(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    store.append(
        TranscriptItem(
            message_id="m1",
            role="tool",
            content="failed",
            name="shell",
            args={"command": "false"},
            is_error=True,
            policy_reason="exit status",
            security_event_type="tool_error",
        )
    )

    exported = json.loads(store.export_jsonl().read_text().strip())
    assert exported["sequence"] == 1
    assert exported["name"] == "shell"
    assert exported["args"] == {"command": "false"}
    assert exported["is_error"] is True
    assert exported["policy_reason"] == "exit status"
    assert exported["security_event_type"] == "tool_error"


@pytest.mark.parametrize(
    "payload",
    [
        '{"key":1,"key":2}',
        '{"nested":{"key":1,"key":2}}',
    ],
)
def test_read_rejects_duplicate_json_object_keys_at_every_depth(tmp_path, payload):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="tool", content="ok"))
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE transcript SET args_json=?", (payload,))

    with pytest.raises(TranscriptCorruptionError) as exc_info:
        store.read_all()

    assert str(store.path) in str(exc_info.value)
    assert "m1" in str(exc_info.value)


@pytest.mark.parametrize(
    ("payload", "operation"),
    [
        ('{ "a": 1}', "read_all"),
        ('{"z":1,"a":2}', "read_range"),
        ('{"text":"\\u4f60\\u597d"}', "export"),
    ],
)
def test_all_read_paths_reject_noncanonical_args_json(tmp_path, payload, operation):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="tool", content="ok"))
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE transcript SET args_json=?", (payload,))

    with pytest.raises(TranscriptCorruptionError) as exc_info:
        if operation == "read_all":
            store.read_all()
        elif operation == "read_range":
            store.read_range(1, 1)
        else:
            store.export_jsonl()

    assert str(store.path) in str(exc_info.value)
    assert "m1" in str(exc_info.value)


def test_append_requires_an_actual_bool_for_is_error(tmp_path):
    store = TranscriptStore(tmp_path, "s1")

    with pytest.raises((TypeError, ValueError), match="is_error"):
        store.append(TranscriptItem(message_id="m1", role="tool", content="x", is_error=1))

    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT count(*) FROM transcript").fetchone() == (0,)


def test_read_rejects_non_boolean_sqlite_is_error_value(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="tool", content="ok"))
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute("UPDATE transcript SET is_error=2")

    with pytest.raises(TranscriptCorruptionError) as exc_info:
        store.read_all()

    assert "is_error" in str(exc_info.value)
    assert str(store.path) in str(exc_info.value)
    assert "m1" in str(exc_info.value)


def test_transcript_item_takes_a_recursive_immutable_args_snapshot():
    source = {"nested": {"values": [1, {"ok": True}]}}
    item = TranscriptItem(message_id="m1", role="tool", content="ok", args=source)
    source["nested"]["values"][1]["ok"] = False
    source["nested"]["values"].append(2)

    assert item.args == {"nested": {"values": [1, {"ok": True}]}}
    assert deepcopy(item.args) is item.args
    with pytest.raises(TypeError):
        item.args["new"] = "value"
    with pytest.raises(TypeError):
        item.args["nested"]["new"] = "value"
    with pytest.raises((AttributeError, TypeError)):
        item.args["nested"]["values"].append(2)
    with pytest.raises(TypeError):
        item.args["nested"]["values"][1]["ok"] = False


def test_immutable_args_have_no_mutable_builtin_base_class(tmp_path):
    source = {"values": [{"key": "original"}]}
    item = TranscriptItem(message_id="m1", role="tool", content="ok", args=source)
    values = item.args["values"]

    with pytest.raises(TypeError):
        dict.__setitem__(item.args, "injected", True)
    with pytest.raises(TypeError):
        dict.__init__(item.args, {"replaced": True})
    with pytest.raises(TypeError):
        list.append(values, "injected")
    with pytest.raises(TypeError):
        item.args.__init__({"replaced": True})
    with pytest.raises(TypeError):
        values.__init__(["replaced"])

    assert item.args == {"values": [{"key": "original"}]}
    assert values == [{"key": "original"}]
    copied = deepcopy(item)
    assert copied == item
    assert copied.args is item.args

    store = TranscriptStore(tmp_path, "s1")
    store.append(item)
    exported = json.loads(store.export_jsonl().read_text())
    assert exported["args"] == {"values": [{"key": "original"}]}


def test_args_depth_budget_rejects_without_recursion_error(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    value: object = "leaf"
    for _ in range(MAX_ARGS_DEPTH + 1):
        value = {"nested": value}

    with pytest.raises(ValueError, match="depth"):
        store.append(TranscriptItem(message_id="deep", role="tool", content="x", args=value))


def test_args_item_budget_rejects_before_database_write(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    args = {"values": list(range(MAX_ARGS_ITEMS))}

    with pytest.raises(ValueError, match="items"):
        store.append(TranscriptItem(message_id="many", role="tool", content="x", args=args))

    assert store.read_all() == []


def test_args_byte_budget_rejects_before_database_growth(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    before = store.path.stat().st_size
    args = {"payload": "x" * (5 * 1024 * 1024)}

    with pytest.raises(ValueError, match="bytes"):
        store.append(TranscriptItem(message_id="huge", role="tool", content="x", args=args))

    assert store.read_all() == []
    assert store.path.stat().st_size <= before + 4096


def test_read_applies_args_budgets_as_corruption(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="tool", content="ok"))
    oversized = json.dumps({"payload": "x" * (MAX_ARGS_BYTES + 1)}, separators=(",", ":"))
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE transcript SET args_json=?", (oversized,))

    with pytest.raises(TranscriptCorruptionError, match="bytes"):
        store.read_all()


def test_read_deep_json_never_leaks_recursion_error(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    store.append(TranscriptItem(message_id="m1", role="tool", content="ok"))
    payload = '{"nested":' * 2_000 + "null" + "}" * 2_000
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE transcript SET args_json=?", (payload,))

    with pytest.raises(TranscriptCorruptionError) as exc_info:
        store.read_all()

    assert str(store.path) in str(exc_info.value)
    assert "m1" in str(exc_info.value)


def test_duplicate_id_validates_existing_json_before_comparison(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    item = TranscriptItem(message_id="m1", role="tool", content="ok", args={"a": 1})
    store.append(item)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE transcript SET args_json='{ \"a\": 1}'")

    with pytest.raises(TranscriptCorruptionError) as exc_info:
        store.append_and_get_sequence(item)

    assert str(store.path) in str(exc_info.value)
    assert "m1" in str(exc_info.value)
