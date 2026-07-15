from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from core.knowledge import (
    KnowledgeConflictError,
    KnowledgeSourceRoot,
    KnowledgeStore,
)


def _store(tmp_path: Path) -> tuple[KnowledgeStore, Path, Path]:
    vault = tmp_path / "vault"
    vault.mkdir()
    repository = tmp_path / "knowledge"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    store = KnowledgeStore(
        repository,
        tmp_path / "state" / "knowledge.sqlite3",
        {
            "sage-learning": KnowledgeSourceRoot(
                root_id="sage-learning",
                kind="obsidian",
                label="Sage Learning",
                path=vault,
            )
        },
    )
    store.initialize()
    return store, vault, repository


def _legacy_pending(store: KnowledgeStore, relative_path: str) -> str:
    proposal = store.ingest("sage-learning", relative_path)
    legacy_id = "legacy_" + proposal.proposal_id.removeprefix("kprop_")
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "DELETE FROM knowledge_source_understandings WHERE artifact_id=?",
            (proposal.parse_artifact_id,),
        )
        connection.execute(
            "DELETE FROM knowledge_parse_artifacts WHERE artifact_id=?",
            (proposal.parse_artifact_id,),
        )
        connection.execute(
            "UPDATE knowledge_proposals SET proposal_id=?, parse_artifact_id=NULL "
            "WHERE proposal_id=?",
            (legacy_id, proposal.proposal_id),
        )
        connection.commit()
    return legacy_id


def test_ingest_is_content_addressed_idempotent_and_does_not_write_wiki(
    tmp_path: Path,
) -> None:
    store, vault, repository = _store(tmp_path)
    note = vault / "harness.md"
    note.write_text("# Agent Harness\n\n可恢复执行。\n", encoding="utf-8")

    first = store.ingest("sage-learning", "harness.md")
    repeated = store.ingest("sage-learning", "harness.md")

    assert repeated == first
    assert first.status == "pending"
    assert first.revision == 0
    assert first.source_kind == "obsidian"
    assert first.source_revision.startswith("sha256:")
    assert first.raw_path.startswith("raw/sources/obsidian/")
    assert first.parse_artifact_id.startswith("part_")
    artifact = store.get_parse_artifact(first.proposal_id)
    assert artifact is not None
    assert artifact.document.provenance.parser_id == "sage.markdown"
    assert artifact.document.provenance.input_revision == first.source_revision
    assert artifact.document.blocks[0].block_id.startswith("pblk_")
    assert artifact.document.title == "Agent Harness"
    understanding = store.get_source_understanding(first.proposal_id)
    assert understanding is not None
    assert understanding.artifact_id == first.parse_artifact_id
    assert understanding.generator_id == "sage.extractive"
    assert "可恢复执行" in understanding.summary
    assert understanding.citations[0].block_id.startswith("pblk_")
    assert "agent harness" in understanding.topics
    assert (repository / first.raw_path).read_text(encoding="utf-8") == note.read_text(
        encoding="utf-8"
    )
    assert not (repository / first.target_path).exists()
    assert store.summary().source_count == 1
    assert store.summary().pending_proposal_count == 1
    assert "parser_id: sage.markdown" in first.proposed_content


def test_v1_metadata_database_migrates_to_v5_without_rewriting_existing_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state" / "knowledge.sqlite3"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE knowledge_proposals (
                proposal_id TEXT PRIMARY KEY, source_id TEXT NOT NULL,
                source_root_id TEXT NOT NULL, source_kind TEXT NOT NULL,
                source_relative_path TEXT NOT NULL, source_revision TEXT NOT NULL,
                raw_path TEXT NOT NULL, page_id TEXT NOT NULL,
                target_path TEXT NOT NULL, title TEXT NOT NULL,
                proposed_content TEXT NOT NULL, base_page_revision TEXT NOT NULL,
                change_kind TEXT NOT NULL, status TEXT NOT NULL,
                projection_status TEXT NOT NULL, revision INTEGER NOT NULL,
                error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO knowledge_proposals (
                proposal_id, source_id, source_root_id, source_kind,
                source_relative_path, source_revision, raw_path, page_id,
                target_path, title, proposed_content, base_page_revision,
                change_kind, status, projection_status, revision, error,
                created_at, updated_at
            ) VALUES (
                'legacy', 'src_legacy', 'sage-learning', 'obsidian',
                'legacy.md', 'sha256:legacy', 'raw/legacy.md', 'page_legacy',
                'wiki/legacy.md', 'Legacy', '# Legacy', '', 'ingest',
                'pending', 'pending', 0, NULL, '2026-07-15', '2026-07-15'
            )
            """
        )
        connection.execute("PRAGMA user_version=1")
    vault = tmp_path / "vault"
    vault.mkdir()
    repository = tmp_path / "knowledge"
    repository.mkdir()
    store = KnowledgeStore(
        repository,
        database,
        {
            "sage-learning": KnowledgeSourceRoot(
                root_id="sage-learning",
                kind="obsidian",
                label="Sage Learning",
                path=vault,
            )
        },
    )

    store.initialize()

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(knowledge_proposals)")}
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        artifact_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='knowledge_parse_artifacts'"
        ).fetchone()
        policy_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='knowledge_policy_decisions'"
        ).fetchone()
        legacy = connection.execute(
            "SELECT proposal_id, parse_artifact_id FROM knowledge_proposals "
            "WHERE proposal_id='legacy'"
        ).fetchone()
    assert version == 5
    assert "parse_artifact_id" in columns
    assert artifact_table is not None
    assert policy_table is not None
    assert legacy == ("legacy", None)


def test_v2_parse_artifacts_backfill_source_understanding(tmp_path: Path) -> None:
    store, vault, _ = _store(tmp_path)
    note = vault / "legacy.md"
    note.write_text("# Legacy\n\n可追溯的旧解析产物。\n", encoding="utf-8")
    proposal = store.ingest("sage-learning", "legacy.md")
    database = store.database_path

    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM knowledge_source_understandings")
        connection.execute("PRAGMA user_version=2")
        connection.commit()

    migrated = KnowledgeStore(
        store.workspace_root,
        database,
        store.source_roots,
    )
    migrated.initialize()
    understanding = migrated.get_source_understanding(proposal.proposal_id)

    assert understanding is not None
    assert "可追溯的旧解析产物" in understanding.summary
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute(
            "SELECT COUNT(*) FROM knowledge_source_understandings"
        ).fetchone()[0] == 1


def test_pending_migration_reparses_and_auto_applies_legacy_markdown(tmp_path: Path) -> None:
    store, vault, repository = _store(tmp_path)
    (vault / "legacy.md").write_text(
        "# Legacy\n\n这条历史记录应自动沉淀。\n",
        encoding="utf-8",
    )
    legacy_id = _legacy_pending(store, "legacy.md")

    first_plan = store.plan_pending_migration()
    repeated_plan = store.plan_pending_migration()

    assert repeated_plan == first_plan
    assert first_plan.total == 1
    assert first_plan.count("auto_apply") == 1
    assert first_plan.items[0].proposal_id == legacy_id
    assert first_plan.items[0].reason_codes == ("trusted_local_reparse",)

    result = store.execute_pending_migration(first_plan.plan_id)

    assert result.status == "completed"
    assert result.count("auto_applied") == 1
    replacement_id = result.items[0].replacement_proposal_id
    assert replacement_id is not None and replacement_id != legacy_id
    replacement = store.get_proposal(replacement_id)
    assert replacement.status == "approved"
    assert replacement.projection_status == "complete"
    assert replacement.parse_artifact_id is not None
    assert (repository / replacement.target_path).is_file()
    retired = store.get_proposal(legacy_id)
    assert retired.status == "rejected"
    assert retired.error == "migration:superseded_by_reparse"
    assert store.list_events(legacy_id)[-1].detail["replacement_proposal_id"] == replacement_id
    assert store.plan_pending_migration().total == 0


@pytest.mark.parametrize(
    ("change_source", "reason_code"),
    [
        ("delete", "source_missing"),
        ("replace", "source_revision_superseded"),
    ],
)
def test_pending_migration_retires_missing_or_superseded_sources(
    tmp_path: Path,
    change_source: str,
    reason_code: str,
) -> None:
    store, vault, _ = _store(tmp_path)
    source = vault / "legacy.md"
    source.write_text("# Legacy\n\n旧版本。\n", encoding="utf-8")
    legacy_id = _legacy_pending(store, "legacy.md")
    if change_source == "delete":
        source.unlink()
    else:
        source.write_text("# Legacy\n\n新版本。\n", encoding="utf-8")

    plan = store.plan_pending_migration()

    assert plan.total == 1
    assert plan.items[0].disposition == "retire"
    assert plan.items[0].reason_codes == (reason_code,)
    result = store.execute_pending_migration(plan.plan_id)
    assert result.count("retired") == 1
    assert store.get_proposal(legacy_id).error == f"migration:{reason_code}"


def test_pending_migration_rejects_a_stale_plan(tmp_path: Path) -> None:
    store, vault, _ = _store(tmp_path)
    source = vault / "legacy.md"
    source.write_text("# Legacy\n\n旧版本。\n", encoding="utf-8")
    _legacy_pending(store, "legacy.md")
    plan = store.plan_pending_migration()
    source.write_text("# Legacy\n\n计划生成后变化。\n", encoding="utf-8")

    with pytest.raises(KnowledgeConflictError, match="migration plan changed"):
        store.execute_pending_migration(plan.plan_id)


def test_ingest_rejects_traversal_and_symlink_sources(tmp_path: Path) -> None:
    store, vault, _ = _store(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# Secret\n", encoding="utf-8")
    (vault / "linked.md").symlink_to(outside)

    with pytest.raises(ValueError, match="relative source path"):
        store.ingest("sage-learning", "../outside.md")
    with pytest.raises(ValueError, match="symbolic link"):
        store.ingest("sage-learning", "linked.md")
    with pytest.raises(KeyError):
        store.ingest("unknown", "harness.md")

    (vault / "credential.md").write_text(
        "# Credential\n\nOPENAI_API_KEY=sk-1234567890abcdefghijklmnop\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="secret material"):
        store.ingest("sage-learning", "credential.md")


def test_ingest_html_uses_parser_output_and_preserves_raw_snapshot(tmp_path: Path) -> None:
    store, vault, repository = _store(tmp_path)
    source = vault / "guide.html"
    source.write_text(
        "<html><head><title>Sage Guide</title><script>alert(1)</script></head>"
        "<body><h1>Harness</h1><p>可恢复执行。</p></body></html>",
        encoding="utf-8",
    )

    proposal = store.ingest("sage-learning", "guide.html")
    artifact = store.get_parse_artifact(proposal.proposal_id)

    assert artifact is not None
    assert artifact.document.provenance.parser_id == "sage.html"
    assert proposal.raw_path.endswith(".html")
    assert (repository / proposal.raw_path).read_bytes() == source.read_bytes()
    assert "可恢复执行" in proposal.proposed_content
    assert "alert" not in proposal.proposed_content


def test_approve_updates_git_wiki_and_reject_is_terminal(tmp_path: Path) -> None:
    store, vault, repository = _store(tmp_path)
    (vault / "harness.md").write_text("# Agent Harness\n\n第一版。\n", encoding="utf-8")
    proposal = store.ingest("sage-learning", "harness.md")

    approved = store.approve(proposal.proposal_id, expected_revision=0)

    assert approved.status == "approved"
    assert approved.projection_status == "complete"
    assert approved.revision == 1
    assert "第一版" in (repository / proposal.target_path).read_text(encoding="utf-8")
    assert proposal.target_path in (repository / "index.md").read_text(encoding="utf-8")
    assert proposal.proposal_id in (repository / "log.md").read_text(encoding="utf-8")
    assert len(store.list_pages()) == 1
    assert len(store.list_pages()[0].revisions) == 1
    commits = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    assert int(commits.stdout.strip()) == 2

    (vault / "rejected.md").write_text("# Rejected\n", encoding="utf-8")
    rejected_proposal = store.ingest("sage-learning", "rejected.md")
    rejected = store.reject(rejected_proposal.proposal_id, expected_revision=0)
    assert rejected.status == "rejected"
    with pytest.raises(KnowledgeConflictError):
        store.approve(rejected.proposal_id, expected_revision=1)


def test_low_risk_local_ingest_auto_applies_once_and_persists_policy(tmp_path: Path) -> None:
    store, vault, repository = _store(tmp_path)
    (vault / "learning.md").write_text(
        "# Learning\n\n确定性来源自动沉淀。\n", encoding="utf-8"
    )
    proposal = store.ingest("sage-learning", "learning.md")

    first = store.evaluate_and_apply_policy(proposal.proposal_id)
    repeated = store.evaluate_and_apply_policy(proposal.proposal_id)
    decision = store.get_policy_decision(proposal.proposal_id)

    assert first == repeated
    assert first.status == "approved"
    assert first.projection_status == "complete"
    assert decision is not None
    assert decision.policy_version == "1.0.0"
    assert decision.risk_level == "low"
    assert decision.action == "auto_apply"
    assert decision.applied_page_revision == store.list_pages()[0].current_revision
    assert "确定性来源自动沉淀" in (repository / first.target_path).read_text(
        encoding="utf-8"
    )
    assert len(store.list_pages()[0].revisions) == 1
    assert [event.event_type for event in store.list_events(first.proposal_id)].count(
        "policy_evaluated"
    ) == 1


def test_policy_does_not_relabel_a_historical_manual_approval(tmp_path: Path) -> None:
    store, vault, _ = _store(tmp_path)
    (vault / "manual.md").write_text("# Manual\n\n人工批准。\n", encoding="utf-8")
    proposal = store.ingest("sage-learning", "manual.md")
    approved = store.approve(proposal.proposal_id, 0)

    repeated = store.evaluate_and_apply_policy(approved.proposal_id)

    assert repeated == approved
    assert store.get_policy_decision(approved.proposal_id) is None


def test_undo_first_auto_apply_adds_retraction_revision_and_is_single_use(
    tmp_path: Path,
) -> None:
    store, vault, repository = _store(tmp_path)
    (vault / "learning.md").write_text("# Learning\n\n第一版。\n", encoding="utf-8")
    original = store.ingest("sage-learning", "learning.md")
    store.evaluate_and_apply_policy(original.proposal_id)
    applied_revision = store.list_pages()[0].current_revision

    undone = store.undo_auto_apply(
        original.proposal_id, expected_page_revision=applied_revision
    )
    page = store.list_pages()[0]
    decision = store.get_policy_decision(original.proposal_id)

    assert undone.change_kind == "retraction"
    assert undone.status == "approved"
    assert len(page.revisions) == 2
    assert page.revisions[-1].change_kind == "retraction"
    assert "此自动沉淀已由用户撤销" in (repository / page.path).read_text(
        encoding="utf-8"
    )
    assert decision is not None
    assert decision.undo_proposal_id == undone.proposal_id
    assert decision.undo_page_revision == page.current_revision
    assert decision.undone_at is not None
    with pytest.raises(KnowledgeConflictError):
        store.undo_auto_apply(original.proposal_id, expected_page_revision=applied_revision)


def test_undo_auto_apply_refuses_to_overwrite_a_newer_revision(tmp_path: Path) -> None:
    store, vault, _ = _store(tmp_path)
    note = vault / "learning.md"
    note.write_text("# Learning\n\n第一版。\n", encoding="utf-8")
    original = store.ingest("sage-learning", "learning.md")
    store.evaluate_and_apply_policy(original.proposal_id)
    original_revision = store.list_pages()[0].current_revision

    note.write_text("# Learning\n\n第二版。\n", encoding="utf-8")
    second = store.ingest("sage-learning", "learning.md")
    store.evaluate_and_apply_policy(second.proposal_id)

    with pytest.raises(KnowledgeConflictError, match="page revision conflict"):
        store.undo_auto_apply(
            original.proposal_id,
            expected_page_revision=original_revision,
        )


def test_workspace_synthesis_uses_only_approved_sources_and_is_reviewable(
    tmp_path: Path,
) -> None:
    store, vault, repository = _store(tmp_path)
    (vault / "approved.md").write_text(
        "# Approved\n\n已批准来源证据。\n", encoding="utf-8"
    )
    (vault / "pending.md").write_text(
        "# Pending\n\n不能进入总览。\n", encoding="utf-8"
    )
    approved = store.ingest("sage-learning", "approved.md")
    store.approve(approved.proposal_id, 0)
    pending = store.ingest("sage-learning", "pending.md")

    synthesis = store.propose_workspace_synthesis()
    repeated = store.propose_workspace_synthesis()
    artifact = store.get_workspace_synthesis(synthesis.proposal_id)

    assert repeated == synthesis
    assert synthesis.change_kind == "synthesis"
    assert synthesis.status == "pending"
    assert "已批准来源证据" in synthesis.proposed_content
    assert "不能进入总览" not in synthesis.proposed_content
    assert pending.proposal_id not in synthesis.proposed_content
    assert artifact is not None
    assert len(artifact.sources) == 1
    assert artifact.sources[0].proposal_id == approved.proposal_id
    assert artifact.sources[0].citation_block_ids
    assert (repository / "overview.md").read_text(encoding="utf-8") == (
        "# Overview\n\n尚无已批准知识页面。\n"
    )

    projected = store.approve(synthesis.proposal_id, 0)

    assert projected.projection_status == "complete"
    assert "已批准来源证据" in (repository / "overview.md").read_text(encoding="utf-8")
    page = next(item for item in store.list_pages() if item.page_id == "page_workspace_overview")
    assert page.revisions[-1].change_kind == "synthesis"


def test_stale_proposal_conflicts_and_rollback_creates_new_revision(
    tmp_path: Path,
) -> None:
    store, vault, repository = _store(tmp_path)
    note = vault / "harness.md"
    note.write_text("# Agent Harness\n\n第一版。\n", encoding="utf-8")
    store.approve(store.ingest("sage-learning", "harness.md").proposal_id, 0)
    page = store.list_pages()[0]
    first_revision = page.revisions[0]

    note.write_text("# Agent Harness\n\n第二版。\n", encoding="utf-8")
    second_proposal = store.ingest("sage-learning", "harness.md")
    second = store.approve(second_proposal.proposal_id, 0)
    assert second.status == "approved"
    current = store.list_pages()[0]
    assert len(current.revisions) == 2
    assert "第二版" in (repository / current.path).read_text(encoding="utf-8")

    rollback = store.propose_rollback(
        current.page_id,
        target_revision_id=first_revision.revision_id,
        expected_page_revision=current.current_revision,
    )
    assert rollback.status == "pending"
    assert rollback.parse_artifact_id is None
    assert store.get_parse_artifact(rollback.proposal_id) is None
    assert "第二版" in (repository / current.path).read_text(encoding="utf-8")
    store.approve(rollback.proposal_id, expected_revision=0)

    restored = store.list_pages()[0]
    assert len(restored.revisions) == 3
    assert "第一版" in (repository / restored.path).read_text(encoding="utf-8")
    assert restored.revisions[-1].change_kind == "rollback"

    note.write_text("# Agent Harness\n\n第三版。\n", encoding="utf-8")
    stale = store.ingest("sage-learning", "harness.md")
    (repository / restored.path).write_text("manual edit\n", encoding="utf-8")
    with pytest.raises(KnowledgeConflictError, match="changed outside Sage"):
        store.approve(stale.proposal_id, expected_revision=0)
