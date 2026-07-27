from __future__ import annotations

import subprocess
from pathlib import Path

from core.knowledge.eval_runner import (
    GateObservation,
    calibrate_gate,
    run_sqlite_layered_eval,
)
from core.knowledge.index import LocalKnowledgeIndex
from core.knowledge.store import KnowledgeSourceRoot, KnowledgeStore

REPO_ROOT = Path(__file__).parents[3]
DATASET_PATH = REPO_ROOT / "knowledge" / "eval" / "dataset.json"


class _NonSemanticTestProvider:
    model_id = "test.non-semantic"
    model_revision = "1"
    dimensions = 2
    supports_semantic_recall = False

    def embed(self, text: str) -> tuple[float, ...]:
        normalized = text.casefold()
        if "meaning" in normalized or "unrelated" in normalized:
            return (1.0, 0.0)
        return (0.0, 1.0)


def test_search_modes_are_isolated_and_default_hybrid_behavior_is_unchanged(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "note.md").write_text(
        "# Topic\n\nUnrelated lexical words are embedded together.\n",
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
            workspace_id="eval-test",
            embedding_provider=_NonSemanticTestProvider(),
        ),
    )
    proposal = store.ingest("test", "note.md")
    store.approve(proposal.proposal_id, proposal.revision)

    assert store.search("meaning", retrieval_mode="sparse") == ()
    dense = store.search("meaning", retrieval_mode="dense")
    assert dense[0].retrieval_route == "dense"
    assert dense[0].sparse_rank is None
    assert dense[0].dense_rank == 1
    assert store.search("meaning") == ()


def test_gate_calibration_preserves_answerable_recall_before_optimizing_abstention() -> None:
    result = calibrate_gate(
        (
            GateObservation("a-high", True, 0.90),
            GateObservation("a-mid", True, 0.80),
            GateObservation("a-low", True, 0.70),
            GateObservation("n-low", False, 0.20),
            GateObservation("n-mid", False, 0.75),
        ),
        minimum_answerable_recall=0.90,
    )

    assert result.threshold == 0.70
    assert result.case_count == 5
    assert result.answerable_count == 3
    assert result.unanswerable_count == 2
    assert result.metrics["answerable_recall"] == 1.0
    assert result.metrics["abstain_recall"] == 0.5


def test_committed_dataset_produces_reproducible_layered_sqlite_report() -> None:
    first = run_sqlite_layered_eval(
        REPO_ROOT,
        DATASET_PATH,
        retrieval_modes=("sparse",),
        top_k=10,
        candidate_k=30,
        token_budget=3_000,
    )
    repeated = run_sqlite_layered_eval(
        REPO_ROOT,
        DATASET_PATH,
        retrieval_modes=("sparse",),
        top_k=10,
        candidate_k=30,
        token_budget=3_000,
    )

    assert first["dataset"] == {
        "dataset_id": "sage-official-agent-fullstack-v1",
        "dataset_revision": "2026-07-27.1",
        "case_count": 80,
        "corpus_count": 9,
        "split_counts": {"dev": 40, "calibration": 20, "test": 20},
        "frozen_test": True,
    }
    route = first["routes"]["sparse"]
    assert route["gate"]["calibration_split"] == "calibration"
    assert route["gate"]["case_count"] == 20
    assert route["splits"]["test"]["case_count"] == 20
    assert route["generation"]["evaluator"] == "deterministic_extractive_proxy"
    assert route["generation"]["llm_judge_used"] is False
    assert route["failures"]["grounding"] == 0
    assert route["system"]["estimated_cost_usd"] == 0.0
    assert first["provider"]["supports_semantic_recall"] is False
    assert first["deterministic_digest"].startswith("sha256:")
    assert repeated["deterministic_digest"] == first["deterministic_digest"]
    assert all(
        case["primary_failure"]
        in {
            "none",
            "ingestion",
            "retrieval",
            "ranking",
            "context",
            "false_rejection",
            "false_acceptance",
            "grounding",
            "citation",
            "system",
        }
        for case in route["cases"]
    )
    assert all("answer_text" not in case for case in route["cases"])
