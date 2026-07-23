import json
import os
import sqlite3
from pathlib import Path

import pytest

from core.coding.memory import MemoryManager
from core.coding.persistence.memory_store import (
    MAX_JSON_BYTES,
    MemoryCandidate,
    MemoryConflictError,
    MemoryCorruptionError,
    MemoryStore,
    MemoryStoreError,
)


def test_proposal_restart_and_no_mutation_before_approval(tmp_path: Path) -> None:
    ws = tmp_path / "repo"
    ws.mkdir()
    manager = MemoryManager(tmp_path / "storage", ws)
    proposal = manager.create_proposal(
        [MemoryCandidate("use ruff")], run_id="run-1", reflection_id="r-1"
    )
    assert manager.memory_store.list_facts() == []
    reopened = MemoryManager(tmp_path / "storage", ws)
    assert reopened.get_proposal(proposal.proposal_id) == proposal
    approved = reopened.approve(proposal.proposal_id, expected_revision=0)
    assert approved.status == "approved"
    assert [f.content for f in reopened.memory_store.list_facts()] == ["use ruff"]


def test_workspace_isolation_and_duplicate_hash(tmp_path: Path) -> None:
    a = MemoryStore(tmp_path / "storage", "workspace-a")
    b = MemoryStore(tmp_path / "storage", "workspace-b")
    a.create_proposal([MemoryCandidate("same")], proposal_id="a")
    a.approve("a", 0)
    b.create_proposal([MemoryCandidate("same")], proposal_id="b")
    b.approve("b", 0)
    assert len(a.list_facts()) == len(b.list_facts()) == 1
    c = a.create_proposal([MemoryCandidate("same")], proposal_id="a2")
    a.approve(c.proposal_id, 0)
    assert len(a.list_facts()) == 1


def test_cas_idempotency_and_audit_events(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "storage", "workspace")
    store.create_proposal([MemoryCandidate("fact")], proposal_id="p")
    with pytest.raises(MemoryConflictError):
        store.approve("p", expected_revision=4)
    approved = store.approve("p", expected_revision=0)
    assert store.approve("p", expected_revision=1) == approved
    with pytest.raises(MemoryConflictError):
        store.reject("p", expected_revision=1)
    events = store.list_events("p")
    assert [e.event_type for e in events] == ["proposal_created", "proposal_approved"]
    assert events[0].candidate_count == 1


def test_manager_approval_replay_does_not_duplicate_markdown_projection(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manager = MemoryManager(tmp_path / "storage", workspace)
    proposal = manager.create_proposal([MemoryCandidate("once")], proposal_id="once")
    manager.approve(proposal.proposal_id, 0)
    first = len(manager.durable.list_facts())
    manager.approve(proposal.proposal_id, 1)
    assert len(manager.durable.list_facts()) == first


def test_projection_failure_is_replayed_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    manager = MemoryManager(tmp_path / "storage", workspace)
    proposal = manager.create_proposal([MemoryCandidate("recover")], proposal_id="recover")
    original = manager.durable.approve_dream
    monkeypatch.setattr(
        manager.durable, "approve_dream", lambda facts: (_ for _ in ()).throw(OSError("disk"))
    )
    with pytest.raises(OSError):
        manager.approve(proposal.proposal_id, 0)
    reopened = MemoryManager(tmp_path / "storage", workspace)
    assert [f.content for f in reopened.durable.list_facts()] == ["recover"]
    topic = reopened.durable.root / "project-conventions.md"
    assert topic.read_text(encoding="utf-8").count('"content": "recover"') == 1
    monkeypatch.setattr(manager.durable, "approve_dream", original)


@pytest.mark.parametrize("bad", [".", "..", "a/b", "a\\b", "", " space"])
def test_store_rejects_unsafe_workspace_ids(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        MemoryStore(tmp_path / "storage", bad)


def test_reject_ignores_fact_base_staleness(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "storage", "workspace")
    first = store.create_proposal([MemoryCandidate("one")], proposal_id="one")
    second = store.create_proposal([MemoryCandidate("two")], proposal_id="two")
    store.approve(first.proposal_id, 0)
    rejected = store.reject(second.proposal_id, 0)
    assert rejected.status == "rejected"


def test_same_proposal_id_requires_metadata_match(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "storage", "workspace")
    store.create_proposal([MemoryCandidate("same")], proposal_id="p", run_id="r1")
    with pytest.raises(MemoryConflictError):
        store.create_proposal([MemoryCandidate("same")], proposal_id="p", run_id="r2")


def test_legacy_v0_proposal_schema_migrates(tmp_path: Path) -> None:
    root = tmp_path / "storage" / "memory" / "workspace"
    root.mkdir(parents=True)
    path = root / "memory.sqlite3"
    db = sqlite3.connect(path)
    db.executescript("""
    CREATE TABLE memory_facts (content_hash TEXT PRIMARY KEY, content TEXT NOT NULL, topic TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT NOT NULL, created_at TEXT NOT NULL, proposal_id TEXT NOT NULL DEFAULT '');
    CREATE TABLE memory_proposals (proposal_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, candidates_json TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL, session_id TEXT NOT NULL DEFAULT '', run_id TEXT NOT NULL DEFAULT '', reflection_id TEXT NOT NULL DEFAULT '', base_revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE memory_events (event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, proposal_id TEXT NOT NULL, workspace_id TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '', run_id TEXT NOT NULL DEFAULT '', reflection_id TEXT NOT NULL DEFAULT '', candidate_count INTEGER NOT NULL, base_revision INTEGER NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL);
    CREATE INDEX memory_events_proposal_idx ON memory_events(proposal_id, created_at);
    PRAGMA user_version=0;
    """)
    candidates = json.dumps(
        [
            {
                "content": "legacy fact",
                "topic": "project-conventions",
                "source": "dream_proposal",
                "source_ref": "run-1",
                "created_at": "2026-07-12T00:00:00+00:00",
            }
        ]
    )
    db.execute(
        "INSERT INTO memory_proposals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-approved",
            "workspace",
            candidates,
            "approved",
            1,
            "session-1",
            "run-1",
            "reflection-1",
            0,
            "2026-07-12T00:00:00+00:00",
            "2026-07-12T00:00:00+00:00",
        ),
    )
    db.commit()
    db.close()
    store = MemoryStore(tmp_path / "storage", "workspace")
    assert store.path.exists()
    with sqlite3.connect(store.path) as check:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            check.execute(
                "SELECT projection_status FROM memory_proposals WHERE proposal_id=?",
                ("legacy-approved",),
            ).fetchone()[0]
            == "pending"
        )


def test_legacy_migration_rolls_back_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.coding.persistence import memory_store as module

    root = tmp_path / "storage" / "memory" / "workspace"
    root.mkdir(parents=True)
    path = root / "memory.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE memory_facts (content_hash TEXT PRIMARY KEY, content TEXT NOT NULL, topic TEXT NOT NULL, source TEXT NOT NULL, source_ref TEXT NOT NULL, created_at TEXT NOT NULL, proposal_id TEXT NOT NULL DEFAULT '');
        CREATE TABLE memory_proposals (proposal_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, candidates_json TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL, session_id TEXT NOT NULL DEFAULT '', run_id TEXT NOT NULL DEFAULT '', reflection_id TEXT NOT NULL DEFAULT '', base_revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE memory_events (event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, proposal_id TEXT NOT NULL, workspace_id TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '', run_id TEXT NOT NULL DEFAULT '', reflection_id TEXT NOT NULL DEFAULT '', candidate_count INTEGER NOT NULL, base_revision INTEGER NOT NULL, revision INTEGER NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX memory_events_proposal_idx ON memory_events(proposal_id, created_at);
        PRAGMA user_version=0;
        """)

    original_sql = module._PROPOSALS_SQL
    monkeypatch.setattr(module, "_PROPOSALS_SQL", "INVALID SQL")
    with pytest.raises(sqlite3.Error):
        MemoryStore(tmp_path / "storage", "workspace")

    with sqlite3.connect(path) as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_schema")}
        assert "memory_proposals" in names
        assert "memory_proposals_legacy" not in names
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0

    monkeypatch.setattr(module, "_PROPOSALS_SQL", original_sql)
    assert MemoryStore(tmp_path / "storage", "workspace").path == path


def test_store_rejects_symlink_in_storage_path(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises((ValueError, MemoryStoreError)):
        MemoryStore(link / "nested", "workspace")


def test_store_rejects_database_hardlink_added_after_construction(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "storage", "workspace")
    os.link(store.path, tmp_path / "database-copy")

    with pytest.raises(MemoryCorruptionError):
        store.list_facts()


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_store_rejects_unsafe_sqlite_sidecar(tmp_path: Path, suffix: str) -> None:
    store = MemoryStore(tmp_path / "storage", "workspace")
    victim = tmp_path / "victim"
    victim.write_text("safe", encoding="utf-8")
    sidecar = Path(f"{store.path}{suffix}")
    sidecar.unlink(missing_ok=True)
    sidecar.symlink_to(victim)

    with pytest.raises(MemoryCorruptionError):
        store.list_facts()
    assert victim.read_text(encoding="utf-8") == "safe"


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_store_rejects_hardlinked_sqlite_sidecar(tmp_path: Path, suffix: str) -> None:
    store = MemoryStore(tmp_path / "storage", "workspace")
    sidecar = Path(f"{store.path}{suffix}")
    sidecar.unlink(missing_ok=True)
    sidecar.write_bytes(b"")
    os.link(sidecar, tmp_path / f"sidecar-copy{suffix}")

    with pytest.raises(MemoryCorruptionError):
        store.list_facts()


@pytest.mark.parametrize(
    "schema_sql",
    [
        "CREATE TABLE surprise(value TEXT)",
        "CREATE VIEW surprise AS SELECT * FROM memory_facts",
        "CREATE TRIGGER surprise AFTER INSERT ON memory_facts BEGIN SELECT 1; END",
        "CREATE INDEX surprise ON memory_facts(content)",
    ],
)
def test_store_rejects_noncanonical_schema_objects(tmp_path: Path, schema_sql: str) -> None:
    store = MemoryStore(tmp_path / "storage", "workspace")
    with sqlite3.connect(store.path) as db:
        db.execute(schema_sql)

    with pytest.raises(MemoryStoreError):
        MemoryStore(tmp_path / "storage", "workspace")


@pytest.mark.parametrize("version", [-1, 2])
def test_store_rejects_unsupported_schema_versions(tmp_path: Path, version: int) -> None:
    store = MemoryStore(tmp_path / "storage", "workspace")
    with sqlite3.connect(store.path) as db:
        db.execute(f"PRAGMA user_version={version}")

    with pytest.raises(MemoryStoreError):
        MemoryStore(tmp_path / "storage", "workspace")


@pytest.mark.parametrize(
    "payload",
    [
        '[{"content":"one","content":"two"}]',
        '[{"content":NaN}]',
        "{}",
        '[{"content":4}]',
        '[{"content":"ok","unknown":"field"}]',
    ],
)
def test_store_wraps_malformed_candidate_payload(tmp_path: Path, payload: str) -> None:
    store = MemoryStore(tmp_path / "storage", "workspace")
    store.create_proposal([MemoryCandidate("valid")], proposal_id="p")
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE memory_proposals SET candidates_json=? WHERE proposal_id='p'",
            (payload,),
        )

    with pytest.raises(MemoryCorruptionError):
        store.get_proposal("p")


def test_store_rejects_oversized_candidate_payload_on_read(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "storage", "workspace")
    store.create_proposal([MemoryCandidate("valid")], proposal_id="p")
    payload = json.dumps([{"content": "x" * MAX_JSON_BYTES}])
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE memory_proposals SET candidates_json=? WHERE proposal_id='p'",
            (payload,),
        )

    with pytest.raises(MemoryCorruptionError):
        store.get_proposal("p")
