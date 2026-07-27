from __future__ import annotations

import json
import subprocess
from pathlib import Path

from core.knowledge.eval_runner import (
    GateObservation,
    calibrate_gate,
    compare_layered_reports,
    compare_semantic_provider_reports,
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


class _PreparedTestProvider(_NonSemanticTestProvider):
    def __init__(self) -> None:
        self.prepared: tuple[str, ...] = ()

    def prepare(self, texts: tuple[str, ...]) -> None:
        self.prepared = texts


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
        "full_case_count": 80,
        "corpus_count": 9,
        "split_counts": {"dev": 40, "calibration": 20, "test": 20},
        "manifest_split_counts": {"dev": 40, "calibration": 20, "test": 20},
        "frozen_test": True,
        "frozen_test_evaluated": True,
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


def test_layered_eval_can_select_dev_and_calibration_without_embedding_frozen_test() -> None:
    provider = _PreparedTestProvider()
    report = run_sqlite_layered_eval(
        REPO_ROOT,
        DATASET_PATH,
        retrieval_modes=("sparse",),
        provider=provider,
        evaluation_splits=("dev", "calibration"),
    )

    frozen_test_queries = {
        case["query"]
        for line in (REPO_ROOT / "knowledge/eval/cases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if (case := json.loads(line))
        if case["dataset_split"] == "test"
    }
    assert report["dataset"]["case_count"] == 60
    assert report["dataset"]["split_counts"] == {
        "dev": 40,
        "calibration": 20,
        "test": 0,
    }
    assert report["dataset"]["frozen_test_evaluated"] is False
    assert report["parameters"]["evaluation_splits"] == ["dev", "calibration"]
    assert report["routes"]["sparse"]["splits"]["test"]["case_count"] == 0
    assert frozen_test_queries.isdisjoint(provider.prepared)


def test_layered_eval_requires_calibration_in_selected_splits() -> None:
    import pytest

    with pytest.raises(ValueError, match="calibration"):
        run_sqlite_layered_eval(
            REPO_ROOT,
            DATASET_PATH,
            retrieval_modes=("sparse",),
            evaluation_splits=("test",),
        )


def test_layered_eval_can_apply_frozen_gate_threshold_without_recalibration() -> None:
    report = run_sqlite_layered_eval(
        REPO_ROOT,
        DATASET_PATH,
        retrieval_modes=("sparse",),
        evaluation_splits=("calibration", "test"),
        gate_thresholds={"sparse": 1_000_000.0},
    )

    gate = report["routes"]["sparse"]["gate"]
    assert gate["threshold_source"] == "fixed_policy"
    assert gate["threshold"] == 1_000_000.0
    assert gate["evaluation"]["true_accept"] == 0
    assert gate["evaluation"]["false_reject"] > 0


def test_layered_report_comparison_enforces_recall_gate_and_lists_regressions() -> None:
    def report(recall: float, cases: list[dict[str, object]]) -> dict[str, object]:
        return {
            "dataset": {"dataset_id": "d", "dataset_revision": "r"},
            "inputs": {"cases_sha256": "sha256:c"},
            "provider": {"model_id": "m", "model_revision": "v"},
            "parameters": {"top_k": 10, "candidate_k": 50},
            "routes": {
                "sparse": {
                    "retrieval": {"recall_at_k": recall},
                    "ranking": {"mrr": recall - 0.1, "ndcg_at_k": recall - 0.05},
                    "system": {"latency_ms": {"p50": 1.0, "p95": 2.0}},
                    "cases": cases,
                }
            },
        }

    sqlite = report(
        0.95,
        [
            {
                "case_id": "stable",
                "primary_failure": "none",
                "retrieval": {"recall_at_k": 1.0},
            },
            {
                "case_id": "regressed",
                "primary_failure": "none",
                "retrieval": {"recall_at_k": 1.0},
            },
        ],
    )
    postgres = report(
        0.92,
        [
            {
                "case_id": "stable",
                "primary_failure": "none",
                "retrieval": {"recall_at_k": 1.0},
            },
            {
                "case_id": "regressed",
                "primary_failure": "retrieval",
                "retrieval": {"recall_at_k": 0.0},
            },
        ],
    )

    comparison = compare_layered_reports(sqlite, postgres, recall_tolerance=0.02)

    assert comparison["compatible_inputs"] is True
    assert comparison["overall_passed"] is False
    sparse = comparison["routes"]["sparse"]
    assert sparse["recall_at_k_delta"] == -0.03
    assert sparse["recall_gate_passed"] is False
    assert sparse["regressions"] == [
        {
            "case_id": "regressed",
            "sqlite_failure": "none",
            "postgres_failure": "retrieval",
            "recall_at_k_delta": -1.0,
        }
    ]


def test_semantic_provider_comparison_freezes_activation_gates() -> None:
    def report(
        *,
        semantic: bool,
        overall_recall: float,
        paraphrase_recall: float,
        abstain_f1: float,
        citation_support: float,
        p95: float,
        cost: float | None,
    ) -> dict[str, object]:
        return {
            "dataset": {"dataset_id": "d", "dataset_revision": "r"},
            "inputs": {"cases_sha256": "sha256:c"},
            "provider": {"supports_semantic_recall": semantic},
            "parameters": {
                "top_k": 10,
                "candidate_k": 50,
                "evaluation_splits": ["dev", "calibration"],
            },
            "routes": {
                "hybrid": {
                    "retrieval": {"recall_at_k": overall_recall},
                    "gate": {"evaluation": {"abstain_f1": abstain_f1}},
                    "citation": {"support_rate": citation_support},
                    "system": {
                        "latency_ms": {"p95": p95},
                        "estimated_cost_usd": cost,
                    },
                    "categories": {
                        "semantic_paraphrase": {"retrieval": {"recall_at_k": paraphrase_recall}}
                    },
                }
            },
        }

    comparison = compare_semantic_provider_reports(
        report(
            semantic=False,
            overall_recall=0.94,
            paraphrase_recall=0.80,
            abstain_f1=0.50,
            citation_support=1.0,
            p95=8.0,
            cost=0.0,
        ),
        report(
            semantic=True,
            overall_recall=0.95,
            paraphrase_recall=0.90,
            abstain_f1=0.46,
            citation_support=1.0,
            p95=40.0,
            cost=0.0,
        ),
    )

    assert comparison["overall_passed"] is True
    assert comparison["gates"] == {
        "semantic_paraphrase_recall": {
            "minimum_delta": 0.05,
            "delta": 0.1,
            "passed": True,
        },
        "overall_recall": {
            "minimum_delta": -0.01,
            "delta": 0.01,
            "passed": True,
        },
        "abstain_f1": {
            "minimum_delta": -0.05,
            "delta": -0.04,
            "passed": True,
        },
        "citation_support": {
            "minimum_delta": -0.001,
            "delta": 0.0,
            "passed": True,
        },
        "p95_latency_ms": {"maximum": 100.0, "actual": 40.0, "passed": True},
        "estimated_cost_usd": {"maximum": 0.01, "actual": 0.0, "passed": True},
    }
