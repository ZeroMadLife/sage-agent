from __future__ import annotations

import json
import os
import stat
import threading

import pytest

from core.coding.persistence.transcript_store import TranscriptItem, TranscriptStore


def test_transcript_append_is_idempotent(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    item = TranscriptItem(message_id="m1", role="user", content="hello")

    assert store.append(item) is True
    assert store.append(item) is False

    assert [entry.message_id for entry in store.read_all()] == ["m1"]


def test_transcript_rebuild_preserves_idempotency(tmp_path):
    item = TranscriptItem(message_id="m1", role="assistant", content="answer")
    assert TranscriptStore(tmp_path, "s1").append(item) is True

    rebuilt = TranscriptStore(tmp_path, "s1")

    assert rebuilt.append(item) is False
    assert rebuilt.read_all() == [item]


def test_transcript_round_trips_unicode_and_metadata(tmp_path):
    item = TranscriptItem(
        message_id="m1",
        role="tool",
        content="你好，世界 🌍",
        run_id="run_1",
        turn_id="turn_1",
        call_id="call_1",
        artifact_ref="artifact.txt",
        created_at="2026-07-11T12:00:00Z",
    )
    store = TranscriptStore(tmp_path, "session")

    assert store.append(item) is True

    assert store.read_all() == [item]
    assert "你好，世界 🌍" in store.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("session_id", ["", ".", "..", "nested/session", r"nested\session"])
def test_transcript_rejects_invalid_session_ids(tmp_path, session_id):
    with pytest.raises(ValueError):
        TranscriptStore(tmp_path, session_id)


def test_two_preconstructed_stores_append_same_message_once(tmp_path):
    stores = [TranscriptStore(tmp_path, "s1"), TranscriptStore(tmp_path, "s1")]
    item = TranscriptItem(message_id="m1", role="user", content="hello")
    barrier = threading.Barrier(2)
    results: list[bool] = []

    def append(store):
        barrier.wait()
        results.append(store.append(item))

    threads = [threading.Thread(target=append, args=(store,)) for store in stores]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results) == [False, True]
    assert stores[0].read_all() == [item]


def test_append_reconciles_complete_line_after_fsync_failure(tmp_path, monkeypatch):
    store = TranscriptStore(tmp_path, "s1")
    item = TranscriptItem(message_id="m1", role="user", content="hello")
    real_fsync = os.fsync
    calls = 0

    def fail_once(fd):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("fsync failed")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_once)

    with pytest.raises(OSError, match="fsync failed"):
        store.append(item)

    assert store.append(item) is False
    assert store.read_all() == [item]
    assert len(store.path.read_text(encoding="utf-8").splitlines()) == 1


def test_torn_tail_is_quarantined_and_valid_prefix_remains_appendable(tmp_path):
    first = TranscriptItem(message_id="m1", role="user", content="hello")
    second = TranscriptItem(message_id="m2", role="assistant", content="world")
    path = tmp_path / "evidence" / "s1" / "transcript.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(first.__dict__) + '\n{"message_id":', encoding="utf-8")

    store = TranscriptStore(tmp_path, "s1")

    assert store.read_all() == [first]
    assert store.append(second) is True
    assert store.read_all() == [first, second]
    quarantines = list(path.parent.glob("transcript.jsonl.torn*"))
    assert len(quarantines) == 1
    assert quarantines[0].read_text(encoding="utf-8") == '{"message_id":'
    assert stat.S_IMODE(quarantines[0].stat().st_mode) == 0o600


def test_malformed_middle_line_reports_path_and_line_number(tmp_path):
    first = TranscriptItem(message_id="m1", role="user", content="hello")
    second = TranscriptItem(message_id="m2", role="assistant", content="world")
    path = tmp_path / "evidence" / "s1" / "transcript.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(first.__dict__) + "\nnot-json\n" + json.dumps(second.__dict__) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(Exception) as exc_info:
        TranscriptStore(tmp_path, "s1")

    assert exc_info.type.__name__ == "TranscriptCorruptionError"
    assert str(path) in str(exc_info.value)
    assert "line 2" in str(exc_info.value)


def test_transcript_file_is_private_when_created_and_reopened(tmp_path):
    store = TranscriptStore(tmp_path, "s1")
    item = TranscriptItem(message_id="m1", role="user", content="hello")

    store.append(item)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE((store.path.parent / ".transcript.lock").stat().st_mode) == 0o600

    store.path.chmod(0o644)
    TranscriptStore(tmp_path, "s1")
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_complete_json_tail_without_newline_is_reframed_before_append(tmp_path):
    first = TranscriptItem(message_id="m1", role="user", content="hello")
    second = TranscriptItem(message_id="m2", role="assistant", content="world")
    path = tmp_path / "evidence" / "s1" / "transcript.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(first.__dict__), encoding="utf-8")

    store = TranscriptStore(tmp_path, "s1")
    assert store.append(second) is True

    assert store.read_all() == [first, second]
    assert path.read_bytes().endswith(b"\n")


def test_transcript_rejects_symlinked_session_before_outside_write(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    session_path = tmp_path / "evidence" / "s1"
    session_path.parent.mkdir()
    session_path.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        TranscriptStore(tmp_path, "s1")

    assert list(outside.iterdir()) == []


def test_transcript_rejects_symlinked_file_before_outside_write(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("", encoding="utf-8")
    path = tmp_path / "evidence" / "s1" / "transcript.jsonl"
    path.parent.mkdir(parents=True)
    path.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        TranscriptStore(tmp_path, "s1")

    assert outside.read_text(encoding="utf-8") == ""
