from __future__ import annotations

import subprocess
from pathlib import Path

from core.knowledge.eval_runner import run_sqlite_layered_eval
from core.knowledge.index import LocalKnowledgeIndex
from core.knowledge.retrieval import KnowledgeAblationPolicy
from core.knowledge.store import KnowledgeSourceRoot, KnowledgeStore

REPO_ROOT = Path(__file__).parents[3]
DATASET_PATH = REPO_ROOT / "knowledge" / "eval" / "dataset.json"


def test_parent_child_policy_is_shared_by_index_and_store(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    parent = "".join(
        f"父块保留完整证据，第 {index} 个子块只负责检索。" for index in range(12)
    )
    (source / "note.md").write_text(
        f"# Sage\n\n## Parent Child\n\n{parent}\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    policy = KnowledgeAblationPolicy(
        strategy="parent_child",
        parent_child_max_chars=64,
        parent_child_overlap_chars=8,
    )
    store = KnowledgeStore(
        workspace,
        tmp_path / "knowledge.sqlite3",
        {
            "test": KnowledgeSourceRoot(
                root_id="test",
                kind="markdown",
                label="Test",
                path=source,
            )
        },
        knowledge_index=LocalKnowledgeIndex(
            workspace_id="eval-parent-child",
            ablation_policy=policy,
        ),
        ablation_policy=policy,
    )
    proposal = store.ingest("test", "note.md")
    store.approve(proposal.proposal_id, proposal.revision)

    hits = store.search("父块", top_k=20, retrieval_mode="sparse")

    assert store.index_summary().active_chunk_count > 1
    assert len(hits) == 1
    assert hits[0].chunk.text == parent


class _DeterministicReranker:
    model_id = "test.cross-encoder"
    model_revision = "test-revision"
    estimated_cost_usd = 0.0

    def rerank(self, query: str, documents: tuple[str, ...]) -> tuple[float, ...]:
        return tuple(
            float(sum(token.casefold() in document.casefold() for token in query.split()))
            for document in documents
        )


def test_layered_eval_records_cross_encoder_as_one_isolated_strategy() -> None:
    report = run_sqlite_layered_eval(
        REPO_ROOT,
        DATASET_PATH,
        retrieval_modes=("hybrid",),
        evaluation_splits=("dev", "calibration"),
        ablation_policy=KnowledgeAblationPolicy(
            strategy="cross_encoder",
            rerank_top_n=20,
        ),
        reranker=_DeterministicReranker(),
    )

    assert report["ablation"] == {
        "strategy": "cross_encoder",
        "parameters": {
            "parent_child_max_chars": 180,
            "parent_child_overlap_chars": 24,
            "semantic_min_chars": 4000,
            "semantic_min_chunk_chars": 800,
            "semantic_breakpoint_percentile": 95.0,
            "rerank_top_n": 20,
        },
        "reranker": {
            "model_id": "test.cross-encoder",
            "model_revision": "test-revision",
            "estimated_cost_usd": 0.0,
        },
        "corpus_profile": {
            "block_count": 49,
            "parent_child_eligible_block_count": 49,
            "semantic_boundary_eligible_block_count": 0,
        },
    }
    assert any(
        hit["rerank_score"] is not None
        for case in report["routes"]["hybrid"]["cases"]
        for hit in case["hits"]
    )
