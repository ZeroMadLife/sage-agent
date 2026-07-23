from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from core.knowledge import (
    KnowledgeConflictError,
    KnowledgeSourceRoot,
    KnowledgeStore,
    LearningCapability,
    LearningGoalDefinition,
)


def _store(tmp_path: Path) -> tuple[KnowledgeStore, Path]:
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
            "learning": KnowledgeSourceRoot(
                root_id="learning",
                kind="obsidian",
                label="Learning",
                path=vault,
            )
        },
    )
    store.initialize()
    return store, vault


def _apply(store: KnowledgeStore, relative_path: str) -> None:
    proposal = store.ingest("learning", relative_path)
    applied = store.evaluate_and_apply_policy(proposal.proposal_id)
    assert applied.projection_status == "complete"


def _goal() -> LearningGoalDefinition:
    return LearningGoalDefinition(
        goal_id="full-stack-ai-engineer",
        title="成为全栈 AI 应用工程师",
        description="完成 AI 产品从前端到云端交付的闭环。",
        capabilities=(
            LearningCapability(
                capability_id="agent-harness",
                label="Agent Harness",
                description="有状态、可恢复的 Agent 运行时。",
                keywords=("Agent Harness", "Recovery"),
                weight=1.5,
                required=True,
            ),
            LearningCapability(
                capability_id="cloud-delivery",
                label="云端交付",
                description="容器化和自动部署。",
                keywords=("Docker", "Kubernetes", "GitHub Actions"),
                weight=1.0,
                required=True,
            ),
        ),
    )


def test_goal_update_is_git_backed_conflict_checked_and_analysis_is_stable(
    tmp_path: Path,
) -> None:
    store, vault = _store(tmp_path)
    initial = store.learning_goal()
    assert initial.structured is True
    updated = store.update_learning_goal(_goal(), expected_goal_revision=initial.goal_revision)

    assert updated.title == "成为全栈 AI 应用工程师"
    assert updated.goal_revision != initial.goal_revision
    assert updated.git_commit != initial.git_commit
    assert store.learning_goal() == updated
    with pytest.raises(KnowledgeConflictError, match="learning goal revision conflict"):
        store.update_learning_goal(_goal(), expected_goal_revision=initial.goal_revision)

    (vault / "harness.md").write_text(
        "# Agent Harness\n\nRecovery connects [[Tool Runtime]] and [[Missing Skill]].\n",
        encoding="utf-8",
    )
    (vault / "runtime.md").write_text(
        "# Tool Runtime\n\nTools are revision aware.\n", encoding="utf-8"
    )
    _apply(store, "harness.md")
    _apply(store, "runtime.md")

    first = store.analyze_graph()
    repeated = store.analyze_graph()

    assert repeated.snapshot.analysis_revision == first.snapshot.analysis_revision
    assert first.snapshot.algorithm_id == "networkx.louvain"
    assert first.snapshot.algorithm_version == "3.5"
    assert first.snapshot.seed == 42
    assert first.snapshot.resolution == 1.0
    assert first.snapshot.goal_revision == updated.goal_revision
    assert first.snapshot.community_count == len(first.communities)
    assert {metric.node_id for metric in first.node_metrics}
    graph = store.graph_overview()
    source_ids = {node.node_id for node in graph.nodes if node.kind == "source"}
    assert source_ids.isdisjoint(metric.node_id for metric in first.node_metrics)

    alignments = {item.capability_id: item for item in first.alignments}
    assert alignments["agent-harness"].status == "covered"
    assert alignments["agent-harness"].coverage == 1.0
    assert alignments["cloud-delivery"].status == "gap"
    assert alignments["cloud-delivery"].coverage == 0.0
    assert any(item.kind == "missing_concept" for item in first.insights)
    assert any(
        item.kind == "capability_gap" and item.capability_id == "cloud-delivery"
        for item in first.insights
    )


def test_goal_revision_changes_analysis_without_rebuilding_graph(tmp_path: Path) -> None:
    store, vault = _store(tmp_path)
    (vault / "note.md").write_text("# Python\n\nFastAPI service.\n", encoding="utf-8")
    _apply(store, "note.md")
    graph = store.graph_overview()
    initial_analysis = store.analyze_graph()
    initial_goal = store.learning_goal()

    changed = store.update_learning_goal(_goal(), expected_goal_revision=initial_goal.goal_revision)
    changed_analysis = store.analyze_graph()

    assert changed_analysis.snapshot.graph_revision == graph.snapshot.graph_revision
    assert changed_analysis.snapshot.goal_revision == changed.goal_revision
    assert (
        changed_analysis.snapshot.analysis_revision != initial_analysis.snapshot.analysis_revision
    )
