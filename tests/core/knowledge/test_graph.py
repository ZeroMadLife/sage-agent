from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from core.knowledge import (
    KnowledgeGraphError,
    KnowledgeRelevancePolicy,
    KnowledgeSourceRoot,
    KnowledgeStore,
)
from core.knowledge.index import LocalKnowledgeIndex


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


def test_graph_projection_is_deterministic_and_evidence_bound(tmp_path: Path) -> None:
    store, vault = _store(tmp_path)
    (vault / "retrieval.md").write_text(
        "# Hybrid Retrieval\n\nRRF combines sparse and dense retrieval.\n",
        encoding="utf-8",
    )
    (vault / "harness.md").write_text(
        "# Agent Harness\n\nUse [[Hybrid Retrieval]] and [[Missing Skill]].\n",
        encoding="utf-8",
    )
    _apply(store, "retrieval.md")
    _apply(store, "harness.md")

    first = store.graph_overview()
    repeated = store.graph_overview()

    assert repeated.snapshot.graph_revision == first.snapshot.graph_revision
    assert repeated.snapshot.wiki_watermark == first.snapshot.wiki_watermark
    assert first.snapshot.status == "ready"
    assert first.snapshot.warning_count == 0
    assert {node.kind for node in first.nodes} == {"page", "source", "concept"}
    missing = next(node for node in first.nodes if node.label == "Missing Skill")
    assert missing.kind == "concept"
    assert missing.properties == {"missing": True, "wikilink": "Missing Skill"}

    wikilinks = [edge for edge in first.edges if edge.kind == "WIKILINK"]
    assert len(wikilinks) == 2
    for edge in first.edges:
        assert edge.extractor_id == "sage.local-knowledge-graph"
        assert edge.extractor_version == "1.2.0"
        assert edge.confidence == 1.0
        assert edge.evidence
        for evidence in edge.evidence:
            assert evidence.citation_id.startswith("kcite_")
            assert evidence.chunk_id.startswith("kchunk_")
            assert evidence.page_revision.startswith("krev_")
            assert evidence.source_revision.startswith("sha256:")


def test_graph_keeps_old_snapshot_after_current_page_changes(tmp_path: Path) -> None:
    store, vault = _store(tmp_path)
    note = vault / "harness.md"
    note.write_text("# Harness\n\nFirst revision.\n", encoding="utf-8")
    _apply(store, "harness.md")
    first = store.graph_overview()

    note.write_text("# Harness\n\nSecond revision with [[Recovery]].\n", encoding="utf-8")
    _apply(store, "harness.md")

    stale = store.graph_status()
    assert stale is not None
    assert stale.graph_revision == first.snapshot.graph_revision
    assert stale.stale is True

    current = store.graph_overview()
    old = store.graph_overview(graph_revision=first.snapshot.graph_revision)
    assert current.snapshot.graph_revision != first.snapshot.graph_revision
    assert current.snapshot.stale is False
    assert old.snapshot.graph_revision == first.snapshot.graph_revision
    assert old.snapshot.stale is True
    assert not any(node.label == "Recovery" for node in old.nodes)
    assert any(node.label == "Recovery" for node in current.nodes)

    with sqlite3.connect(store.database_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM knowledge_graph_snapshots WHERE status='ready'"
        ).fetchone()[0]
    assert count == 2


def test_graph_filter_pagination_node_and_bounded_neighborhood(tmp_path: Path) -> None:
    store, vault = _store(tmp_path)
    (vault / "a.md").write_text("# A\n\n[[B]] [[C]].\n", encoding="utf-8")
    (vault / "b.md").write_text("# B\n\nEvidence B.\n", encoding="utf-8")
    _apply(store, "a.md")
    _apply(store, "b.md")

    page_view = store.graph_overview(kinds=("page",), limit=1)
    assert len(page_view.nodes) == 1
    assert page_view.has_more is True
    assert page_view.next_offset == 1
    second_page = store.graph_overview(kinds=("page",), offset=1, limit=1)
    assert len(second_page.nodes) == 1
    assert second_page.nodes[0].node_id != page_view.nodes[0].node_id

    center = next(node for node in store.graph_overview().nodes if node.label == "A")
    snapshot, loaded = store.graph_node(center.node_id)
    assert loaded == center
    neighborhood = store.graph_neighborhood(center.node_id, limit=1)
    assert neighborhood.snapshot.graph_revision == snapshot.graph_revision
    assert neighborhood.center == center
    assert len(neighborhood.edges) == 1
    assert 1 <= len(neighborhood.nodes) <= 2


def test_graph_projects_standard_markdown_links_and_expands_retrieval(tmp_path: Path) -> None:
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
        tmp_path / "knowledge.sqlite3",
        {
            "learning": KnowledgeSourceRoot(
                root_id="learning", kind="obsidian", label="Learning", path=vault
            )
        },
    )
    store.initialize()
    (vault / "guide.md").write_text(
        "# Harness Guide\n\n这是不包含关系的背景段落。\n\n"
        "编排线索连接到 [混合检索](retrieval.md)。\n",
        encoding="utf-8",
    )
    (vault / "retrieval.md").write_text(
        "# Hybrid Retrieval\n\nRRF 融合 BM25 与 dense route。\n",
        encoding="utf-8",
    )
    _apply(store, "guide.md")
    _apply(store, "retrieval.md")
    policy = KnowledgeRelevancePolicy(
        benchmark_id="test",
        benchmark_revision="test-v1",
        corpus_revision=store.index_summary().corpus_revision,
        embedding_model="sage.hashing",
        embedding_revision="1.0.0",
        top_k=8,
        min_sparse_score=0.0,
        min_dense_score=None,
    )
    store.knowledge_index = LocalKnowledgeIndex(relevance_policy=policy)

    baseline = store.search("编排线索", top_k=4)
    expanded = store.search("编排线索", top_k=4, relation_expand=True)

    assert all(hit.chunk.source_relative_path != "retrieval.md" for hit in baseline)
    graph_hit = next(hit for hit in expanded if hit.retrieval_route == "graph")
    assert graph_hit.chunk.source_relative_path == "retrieval.md"
    assert graph_hit.graph_edge_id is not None
    assert graph_hit.graph_seed_page_id == baseline[0].chunk.page_id
    assert graph_hit.graph_direction == "outbound"
    assert graph_hit.graph_score is not None and graph_hit.graph_score > 0.0
    link = next(
        edge for edge in store.graph_overview().edges if edge.edge_id == graph_hit.graph_edge_id
    )
    assert graph_hit.graph_evidence_citation_id == link.evidence[0].citation_id
    assert link.properties["anchors"] == ["混合检索"]
    assert link.properties["syntaxes"] == ["markdown_internal"]
    with sqlite3.connect(store.database_path) as connection:
        ordinal = connection.execute(
            "SELECT ordinal FROM knowledge_chunks WHERE chunk_id=?",
            (link.evidence[0].chunk_id,),
        ).fetchone()[0]
    assert ordinal == 1


def test_relation_expansion_requires_a_calibrated_gate(tmp_path: Path) -> None:
    store, vault = _store(tmp_path)
    (vault / "a.md").write_text("# A\n\nSeed [[B]].\n", encoding="utf-8")
    (vault / "b.md").write_text("# B\n\nTarget.\n", encoding="utf-8")
    _apply(store, "a.md")
    _apply(store, "b.md")

    with pytest.raises(ValueError, match="requires a calibrated relevance policy"):
        store.search("Seed", relation_expand=True)


def test_failed_graph_rebuild_is_recorded_without_partial_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, vault = _store(tmp_path)
    (vault / "harness.md").write_text("# Harness\n\nEvidence.\n", encoding="utf-8")
    _apply(store, "harness.md")

    def fail_projection(_connection: sqlite3.Connection):
        raise RuntimeError("private implementation detail")

    monkeypatch.setattr(store.knowledge_graph, "_project", fail_projection)
    with pytest.raises(KnowledgeGraphError, match="knowledge graph rebuild failed"):
        store.rebuild_graph(force=True)

    status = store.graph_status()
    assert status is not None
    assert status.status == "error"
    assert status.error == "knowledge graph rebuild failed"
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM knowledge_graph_nodes").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM knowledge_graph_edges").fetchone()[0] == 0
