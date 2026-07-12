from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.coding.context.summary import CompactionCheckpoint, CompactionResult, CompactionSummary
from core.coding.persistence.compaction_store import (
    CompactionConflictError,
    CompactionCorruptionError,
    CompactionStore,
)

_ANCHOR_KEY = b"test-only-checkpoint-anchor-key-32-bytes"


def _checkpoint(compaction_id: str = "cmp-1") -> CompactionCheckpoint:
    summary = CompactionSummary(goal="ship", source_transcript_range=(3, 7))
    previous_hash = "previous-hash"
    evidence_hash = "evidence-hash"
    summary_hash = hashlib.sha256(
        f"{previous_hash}\n{evidence_hash}\n{summary.render_for_prompt()}".encode()
    ).hexdigest()
    return CompactionCheckpoint(
        compaction_id=compaction_id,
        transcript_start=3,
        transcript_end=7,
        summary=summary,
        summary_hash=summary_hash,
        previous_summary_hash=previous_hash,
        evidence_hash=evidence_hash,
        prefix_hash="prefix-hash",
    )


def _result(compaction_id: str = "cmp-1", *, applied: bool = True) -> CompactionResult:
    return CompactionResult(
        applied=applied,
        projected_history=[{"role": "system", "content": "summary"}],
        checkpoint=_checkpoint(compaction_id) if applied else None,
        before_tokens=100,
        after_tokens=40 if applied else 100,
        archived_items=5 if applied else 0,
        reason="" if applied else "failed",
        compaction_id=compaction_id,
        trigger="auto",
    )


def test_store_persists_private_started_and_completed_artifact(tmp_path) -> None:
    store = CompactionStore(tmp_path, checkpoint_anchor_key=_ANCHOR_KEY)
    store.begin("s1", "cmp-1", {"trigger": "auto", "run_id": "run-1"})
    artifact = store.complete("s1", "cmp-1", _result(), evidence={"range": [3, 7]})

    path = tmp_path / "evidence" / "s1" / "compactions" / "cmp-1.json"
    assert artifact["status"] == "completed"
    assert json.loads(path.read_text()) == artifact
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert store.load_latest_attempt("s1") == artifact
    assert store.load_latest_checkpoint("s1") == _checkpoint()
    assert store.verify_checkpoint("s1", _checkpoint()) is True


def test_store_idempotency_and_terminal_conflicts(tmp_path) -> None:
    store = CompactionStore(tmp_path)
    metadata = {"trigger": "auto", "run_id": "run-1"}
    assert store.begin("s1", "cmp-1", metadata) == store.begin("s1", "cmp-1", metadata)
    with pytest.raises(CompactionConflictError):
        store.begin("s1", "cmp-1", {"trigger": "manual"})

    completed = store.complete("s1", "cmp-1", _result())
    assert store.complete("s1", "cmp-1", _result()) == completed
    with pytest.raises(CompactionConflictError):
        store.fail("s1", "cmp-1", _result(applied=False))


def test_store_records_failed_attempt_and_rejects_completion(tmp_path) -> None:
    store = CompactionStore(tmp_path)
    store.begin("s1", "cmp-1", {"trigger": "auto"})
    failed = store.fail("s1", "cmp-1", _result(applied=False))
    assert failed["status"] == "failed"
    assert store.fail("s1", "cmp-1", _result(applied=False)) == failed
    with pytest.raises(CompactionConflictError):
        store.complete("s1", "cmp-1", _result())


@pytest.mark.parametrize("bad", ["", ".", "..", "../x", "x/y", "x\\y"])
def test_store_rejects_unsafe_ids(tmp_path, bad: str) -> None:
    store = CompactionStore(tmp_path)
    with pytest.raises(ValueError):
        store.begin(bad, "cmp-1", {})
    with pytest.raises(ValueError):
        store.begin("s1", bad, {})


def test_store_rejects_symlink_and_hardlink_targets(tmp_path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    directory = tmp_path / "evidence" / "s1" / "compactions"
    directory.mkdir(parents=True)
    (directory / "cmp-link.json").symlink_to(outside)
    os.link(outside, directory / "cmp-hard.json")
    store = CompactionStore(tmp_path)

    with pytest.raises(ValueError, match="symlink"):
        store.begin("s1", "cmp-link", {})
    with pytest.raises(ValueError, match="hardlink"):
        store.begin("s1", "cmp-hard", {})


def test_checkpoint_verifier_rejects_tampering_and_incomplete_attempts(tmp_path) -> None:
    store = CompactionStore(tmp_path, checkpoint_anchor_key=_ANCHOR_KEY)
    store.begin("s1", "cmp-1", {"trigger": "auto"})
    assert store.verify_checkpoint("s1", _checkpoint()) is False
    store.complete("s1", "cmp-1", _result())
    path = tmp_path / "evidence" / "s1" / "compactions" / "cmp-1.json"
    payload = json.loads(path.read_text())
    payload["checkpoint"]["evidence_hash"] = "tampered"
    path.write_text(json.dumps(payload))

    assert store.verify_checkpoint("s1", _checkpoint()) is False


def test_concurrent_same_id_writes_are_serialized(tmp_path) -> None:
    store = CompactionStore(tmp_path)
    metadata = {"trigger": "auto", "run_id": "run-1"}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.begin("s1", "cmp-1", metadata), range(20)))
    assert all(result == results[0] for result in results)

    with ThreadPoolExecutor(max_workers=8) as pool:
        completed = list(pool.map(lambda _: store.complete("s1", "cmp-1", _result()), range(20)))
    assert all(result == completed[0] for result in completed)


def test_terminal_transition_rejects_mismatched_attempt_payload(tmp_path) -> None:
    store = CompactionStore(tmp_path)
    store.begin("s1", "cmp-1", {"trigger": "auto"})

    with pytest.raises(ValueError, match="compaction_id"):
        store.complete("s1", "cmp-1", _result("cmp-other"))
    with pytest.raises(ValueError, match="applied"):
        store.fail("s1", "cmp-1", _result("cmp-1", applied=True))


def test_verifier_rejects_tampered_artifact_identity(tmp_path) -> None:
    store = CompactionStore(tmp_path, checkpoint_anchor_key=_ANCHOR_KEY)
    store.begin("s1", "cmp-1", {"trigger": "auto"})
    store.complete("s1", "cmp-1", _result())
    path = tmp_path / "evidence" / "s1" / "compactions" / "cmp-1.json"
    payload = json.loads(path.read_text())
    payload["session_id"] = "other"
    path.write_text(json.dumps(payload))

    assert store.verify_checkpoint("s1", _checkpoint()) is False


def test_atomic_replace_failure_preserves_started_state(tmp_path, monkeypatch) -> None:
    store = CompactionStore(tmp_path)
    started = store.begin("s1", "cmp-1", {"trigger": "auto"})
    real_replace = os.replace

    def fail_replace(*args, **kwargs):
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        store.complete("s1", "cmp-1", _result())
    monkeypatch.setattr(os, "replace", real_replace)

    assert store.load("s1", "cmp-1") == started
    directory = tmp_path / "evidence" / "s1" / "compactions"
    assert not list(directory.glob("*.tmp"))


def test_latest_checkpoint_ignores_later_started_and_failed_attempts(tmp_path) -> None:
    store = CompactionStore(tmp_path, checkpoint_anchor_key=_ANCHOR_KEY)
    store.begin("s1", "cmp-1", {"order": 1})
    store.complete("s1", "cmp-1", _result("cmp-1"))
    store.begin("s1", "cmp-2", {"order": 2})
    store.begin("s1", "cmp-3", {"order": 3})
    store.fail("s1", "cmp-3", _result("cmp-3", applied=False))

    assert store.load_latest_attempt("s1")["compaction_id"] == "cmp-3"
    assert store.load_latest_checkpoint("s1") == _checkpoint("cmp-1")


@pytest.mark.parametrize(
    ("field", "value"),
    [("schema_version", 999), ("session_id", "other"), ("compaction_id", "other")],
)
def test_load_and_transition_reject_tampered_artifact_identity(tmp_path, field, value) -> None:
    store = CompactionStore(tmp_path)
    store.begin("s1", "cmp-1", {"trigger": "auto"})
    path = tmp_path / "evidence" / "s1" / "compactions" / "cmp-1.json"
    payload = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload))

    with pytest.raises(CompactionCorruptionError):
        store.load("s1", "cmp-1")
    with pytest.raises(CompactionCorruptionError):
        store.fail("s1", "cmp-1", _result(applied=False))


def test_checkpoint_anchor_rejects_self_consistent_payload_replacement(tmp_path) -> None:
    store = CompactionStore(tmp_path, checkpoint_anchor_key=_ANCHOR_KEY)
    store.begin("s1", "cmp-1", {"trigger": "auto"})
    store.complete("s1", "cmp-1", _result())
    path = tmp_path / "evidence" / "s1" / "compactions" / "cmp-1.json"
    payload = json.loads(path.read_text())
    forged = _checkpoint()
    summary = {**forged.summary.model_dump(mode="json"), "goal": "forged"}
    from core.coding.context.summary import CompactionSummary

    forged_summary = CompactionSummary.model_validate(summary)
    forged_hash = hashlib.sha256(
        (
            f"{forged.previous_summary_hash}\n{forged.evidence_hash}\n"
            f"{forged_summary.render_for_prompt()}"
        ).encode()
    ).hexdigest()
    payload["checkpoint"]["summary"] = summary
    payload["checkpoint"]["summary_hash"] = forged_hash
    payload["result"]["checkpoint"] = payload["checkpoint"]
    path.write_text(json.dumps(payload))

    forged_checkpoint = CompactionCheckpoint(
        **{**forged.__dict__, "summary": forged_summary, "summary_hash": forged_hash}
    )
    assert store.verify_checkpoint("s1", forged_checkpoint) is False
    assert store.load_latest_checkpoint("s1") is None


def test_unconfigured_checkpoint_anchor_fails_closed(tmp_path) -> None:
    store = CompactionStore(tmp_path)
    store.begin("s1", "cmp-1", {"trigger": "auto"})
    store.complete("s1", "cmp-1", _result())
    assert store.verify_checkpoint("s1", _checkpoint()) is False
    assert store.load_latest_checkpoint("s1") is None


def test_directory_fsync_failure_after_replace_confirms_committed_payload(
    tmp_path, monkeypatch
) -> None:
    store = CompactionStore(tmp_path, checkpoint_anchor_key=_ANCHOR_KEY)
    store.begin("s1", "cmp-1", {"trigger": "auto"})
    real_fsync = os.fsync

    def fail_directory_fsync(file_fd: int) -> None:
        if os.path.isdir(f"/dev/fd/{file_fd}"):
            raise OSError("injected directory fsync failure")
        real_fsync(file_fd)

    monkeypatch.setattr(os, "fsync", fail_directory_fsync)
    completed = store.complete("s1", "cmp-1", _result())
    monkeypatch.setattr(os, "fsync", real_fsync)

    assert completed["status"] == "completed"
    assert store.load("s1", "cmp-1") == completed


def test_existing_artifact_and_lock_modes_are_tightened(tmp_path) -> None:
    store = CompactionStore(tmp_path)
    store.begin("s1", "cmp-1", {"trigger": "auto"})
    directory = tmp_path / "evidence" / "s1" / "compactions"
    artifact = directory / "cmp-1.json"
    lock = directory / ".cmp-1.lock"
    artifact.chmod(0o644)
    lock.chmod(0o644)

    store.load("s1", "cmp-1")

    assert artifact.stat().st_mode & 0o777 == 0o600
    assert lock.stat().st_mode & 0o777 == 0o600
