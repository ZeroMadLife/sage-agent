"""Run four independent PR-6 retrieval ablations without test-set tuning."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from core.config.settings import get_settings
from core.knowledge.embeddings import (
    DEFAULT_FASTEMBED_DIMENSIONS,
    DEFAULT_FASTEMBED_MODEL,
    DEFAULT_FASTEMBED_MODEL_REVISION,
    DEFAULT_FASTEMBED_REPOSITORY,
    FastEmbedEmbeddingConfig,
    FastEmbedEmbeddingProvider,
)
from core.knowledge.eval_runner import (
    EvalSplit,
    compare_retrieval_ablation_reports,
    run_postgres_layered_eval,
)
from core.knowledge.reranking import (
    DEFAULT_CROSS_ENCODER_MODEL,
    DEFAULT_CROSS_ENCODER_MODEL_REVISION,
    DEFAULT_CROSS_ENCODER_REPOSITORY,
    FastEmbedCrossEncoderConfig,
    FastEmbedCrossEncoderProvider,
)
from core.knowledge.retrieval import (
    KnowledgeAblationPolicy,
    KnowledgeAblationStrategy,
    KnowledgeReranker,
    KnowledgeRetrievalMode,
)

_STRATEGIES: tuple[KnowledgeAblationStrategy, ...] = (
    "contextual_chunk",
    "parent_child",
    "semantic_boundary",
    "cross_encoder",
)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("selection", "final"), required=True)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=repo_root / "knowledge" / "eval" / "dataset.json",
    )
    parser.add_argument(
        "--postgres-dsn",
        default=settings.knowledge_postgres_dsn or settings.postgres_sync_dsn,
    )
    parser.add_argument("--embedding-cache-dir", type=Path, default=Path("~/.cache/sage/fastembed"))
    parser.add_argument(
        "--reranker-cache-dir",
        type=Path,
        default=Path("~/.cache/sage/fastembed-rerank"),
    )
    parser.add_argument("--selection-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--candidate-k", type=int, default=50)
    parser.add_argument("--token-budget", type=int, default=3_000)
    parser.add_argument("--minimum-answerable-recall", type=float, default=0.90)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--local-files-only", action="store_true")
    args = parser.parse_args()

    if not args.postgres_dsn:
        raise ValueError("PostgreSQL ablation eval requires a DSN")
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    if dirty and not args.allow_dirty:
        raise ValueError("ablation eval report must come from a clean source tree")
    output = args.output.expanduser().resolve()
    if args.stage == "final" and output.exists():
        raise ValueError("refusing to overwrite a frozen-test final report")

    selection: dict[str, Any] | None = None
    selection_evidence: dict[str, str] | None = None
    if args.stage == "final":
        if args.selection_report is None:
            raise ValueError("final stage requires --selection-report")
        selection_path = args.selection_report.expanduser().resolve()
        selection = _load_report(selection_path)
        _assert_selection(selection)
        selection_evidence = {
            "path": selection_path.relative_to(repo_root).as_posix(),
            "sha256": "sha256:" + hashlib.sha256(selection_path.read_bytes()).hexdigest(),
        }

    splits: tuple[EvalSplit, ...] = (
        ("dev", "calibration")
        if args.stage == "selection"
        else ("calibration", "test")
    )
    baseline_threshold = None if selection is None else _threshold(selection["baseline"])
    baseline = _run(
        repo_root=repo_root,
        dataset=args.dataset.resolve(),
        postgres_dsn=args.postgres_dsn,
        policy=KnowledgeAblationPolicy(),
        gate_threshold=baseline_threshold,
        embedding_cache_dir=args.embedding_cache_dir,
        reranker_cache_dir=args.reranker_cache_dir,
        local_files_only=args.local_files_only,
        splits=splits,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        token_budget=args.token_budget,
        minimum_answerable_recall=args.minimum_answerable_recall,
    )

    candidates: dict[str, dict[str, Any]] = {}
    comparisons: dict[str, dict[str, Any]] = {}
    decisions: dict[str, dict[str, Any]] = {}
    for strategy in _STRATEGIES:
        threshold = (
            None if selection is None else _threshold(selection["candidates"][strategy])
        )
        candidate = _run(
            repo_root=repo_root,
            dataset=args.dataset.resolve(),
            postgres_dsn=args.postgres_dsn,
            policy=KnowledgeAblationPolicy(strategy=strategy),
            gate_threshold=threshold,
            embedding_cache_dir=args.embedding_cache_dir,
            reranker_cache_dir=args.reranker_cache_dir,
            local_files_only=args.local_files_only,
            splits=splits,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            token_budget=args.token_budget,
            minimum_answerable_recall=args.minimum_answerable_recall,
        )
        comparison = compare_retrieval_ablation_reports(baseline, candidate)
        selection_passed = (
            bool(comparison["overall_passed"])
            if selection is None
            else bool(selection["comparisons"][strategy]["overall_passed"])
        )
        final_passed = None if selection is None else bool(comparison["overall_passed"])
        candidates[strategy] = candidate
        comparisons[strategy] = comparison
        decisions[strategy] = {
            "selected_without_test": selection_passed,
            "final_confirmation_passed": final_passed,
            "eligible_for_default": selection_passed
            and (True if final_passed is None else final_passed),
        }

    result = {
        "schema_version": 1,
        "evaluation_id": f"sage-retrieval-ablation-postgres-{args.stage}-v1",
        "stage": args.stage,
        "protocol": {
            "selection_splits": ["dev", "calibration"],
            "final_splits": ["calibration", "test"],
            "test_used_for_strategy_or_gate_selection": False,
            "retrieval_mode": "hybrid",
            "recovery_enabled": False,
            "ann_index_used": False,
            "strategies_combined": False,
        },
        "selection_evidence": selection_evidence,
        "baseline": baseline,
        "candidates": candidates,
        "comparisons": comparisons,
        "decisions": decisions,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "stage": args.stage,
                "output": str(output),
                "decisions": decisions,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _run(
    *,
    repo_root: Path,
    dataset: Path,
    postgres_dsn: str,
    policy: KnowledgeAblationPolicy,
    gate_threshold: float | None,
    embedding_cache_dir: Path,
    reranker_cache_dir: Path,
    local_files_only: bool,
    splits: tuple[EvalSplit, ...],
    top_k: int,
    candidate_k: int,
    token_budget: int,
    minimum_answerable_recall: float,
) -> dict[str, Any]:
    provider = FastEmbedEmbeddingProvider(
        FastEmbedEmbeddingConfig(
            model=DEFAULT_FASTEMBED_MODEL,
            repository=DEFAULT_FASTEMBED_REPOSITORY,
            model_revision=DEFAULT_FASTEMBED_MODEL_REVISION,
            dimensions=DEFAULT_FASTEMBED_DIMENSIONS,
            cache_dir=embedding_cache_dir,
            batch_size=32,
            local_files_only=local_files_only,
        )
    )
    reranker: KnowledgeReranker | None = None
    if policy.strategy == "cross_encoder":
        reranker = FastEmbedCrossEncoderProvider(
            FastEmbedCrossEncoderConfig(
                model=DEFAULT_CROSS_ENCODER_MODEL,
                repository=DEFAULT_CROSS_ENCODER_REPOSITORY,
                model_revision=DEFAULT_CROSS_ENCODER_MODEL_REVISION,
                cache_dir=reranker_cache_dir,
                local_files_only=local_files_only,
            )
        )
    thresholds: Mapping[KnowledgeRetrievalMode, float] | None = (
        None if gate_threshold is None else {"hybrid": gate_threshold}
    )
    return run_postgres_layered_eval(
        repo_root,
        dataset,
        postgres_dsn=postgres_dsn,
        retrieval_modes=("hybrid",),
        top_k=top_k,
        candidate_k=candidate_k,
        token_budget=token_budget,
        provider=provider,
        minimum_answerable_recall=minimum_answerable_recall,
        evaluation_splits=splits,
        precache_queries=False,
        gate_thresholds=thresholds,
        ablation_policy=policy,
        reranker=reranker,
    )


def _threshold(report: dict[str, Any]) -> float:
    return float(report["routes"]["hybrid"]["gate"]["threshold"])


def _assert_selection(report: dict[str, Any]) -> None:
    if report.get("stage") != "selection":
        raise ValueError("selection report has the wrong stage")
    if report.get("protocol", {}).get("test_used_for_strategy_or_gate_selection") is not False:
        raise ValueError("selection report did not preserve the frozen test split")
    if set(report.get("candidates", {})) != set(_STRATEGIES):
        raise ValueError("selection report does not contain four isolated strategies")


def _load_report(path: Path) -> dict[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ablation eval report must be a JSON object")
    return cast(dict[str, Any], payload)


if __name__ == "__main__":
    raise SystemExit(main())
