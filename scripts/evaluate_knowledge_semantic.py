"""Evaluate and gate one version-bound semantic provider without test-set tuning."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, cast

from core.config.settings import get_settings
from core.knowledge.embeddings import (
    DEFAULT_FASTEMBED_DIMENSIONS,
    DEFAULT_FASTEMBED_MODEL,
    DEFAULT_FASTEMBED_MODEL_REVISION,
    DEFAULT_FASTEMBED_REPOSITORY,
    DashScopeEmbeddingConfig,
    DashScopeEmbeddingProvider,
    FastEmbedEmbeddingConfig,
    FastEmbedEmbeddingProvider,
    OpenAICompatibleEmbeddingConfig,
    OpenAICompatibleEmbeddingProvider,
)
from core.knowledge.eval_runner import (
    EvalSplit,
    compare_semantic_provider_reports,
    run_postgres_layered_eval,
    run_sqlite_layered_eval,
)
from core.knowledge.relevance import KnowledgeRelevancePolicy
from core.knowledge.retrieval import HashingEmbeddingProvider, KnowledgeRetrievalMode

_MODES: tuple[KnowledgeRetrievalMode, ...] = ("sparse", "dense", "hybrid")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("selection", "final"), required=True)
    parser.add_argument("--backend", choices=("sqlite", "postgres"), default="postgres")
    parser.add_argument(
        "--provider",
        choices=("fastembed", "dashscope", "openai_compatible"),
        default="fastembed",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=repo_root / "knowledge" / "eval" / "dataset.json",
    )
    parser.add_argument(
        "--postgres-dsn",
        default=settings.knowledge_postgres_dsn or settings.postgres_sync_dsn,
    )
    parser.add_argument("--cache-dir", type=Path, default=Path("~/.cache/sage/fastembed"))
    parser.add_argument(
        "--embedding-base-url",
        default=settings.knowledge_embedding_base_url,
    )
    parser.add_argument("--embedding-model", default=settings.knowledge_embedding_model)
    parser.add_argument(
        "--embedding-model-revision",
        default=settings.knowledge_embedding_model_revision,
    )
    parser.add_argument(
        "--embedding-dimensions",
        type=int,
        default=settings.knowledge_embedding_dimensions,
    )
    parser.add_argument("--embedding-batch-size", type=int, default=10)
    parser.add_argument(
        "--embedding-timeout-seconds",
        type=float,
        default=settings.knowledge_embedding_timeout_seconds,
    )
    parser.add_argument(
        "--query-instruct",
        default=settings.knowledge_embedding_query_instruct,
    )
    parser.add_argument(
        "--cost-per-1k-tokens-usd",
        type=float,
        default=settings.knowledge_embedding_cost_per_1k_tokens_usd,
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
        raise ValueError("semantic eval report must come from a clean source tree")
    output = args.output.expanduser().resolve()
    if args.stage == "final" and output.exists():
        raise ValueError("refusing to overwrite a frozen-test final report")

    splits: tuple[EvalSplit, ...]
    baseline_thresholds: dict[KnowledgeRetrievalMode, float] | None = None
    candidate_thresholds: dict[KnowledgeRetrievalMode, float] | None = None
    selection_evidence: dict[str, str] | None = None
    selection_candidate: dict[str, Any] | None = None
    if args.stage == "selection":
        splits = ("dev", "calibration")
    else:
        splits = ("calibration", "test")
        if args.selection_report is None:
            raise ValueError("final stage requires --selection-report")
        selection_path = args.selection_report.expanduser().resolve()
        selection = _load_report(selection_path)
        _assert_passing_selection(selection)
        baseline_thresholds = _thresholds(selection["baseline"])
        candidate_thresholds = _thresholds(selection["candidate"])
        selection_candidate = selection["candidate"]
        selection_evidence = {
            "path": selection_path.relative_to(repo_root).as_posix(),
            "sha256": "sha256:" + hashlib.sha256(selection_path.read_bytes()).hexdigest(),
        }

    provider = _build_provider(args, api_key=settings.knowledge_embedding_api_key)
    if selection_candidate is not None:
        _assert_provider_identity(selection_candidate, provider)
    common: dict[str, Any] = {
        "retrieval_modes": _MODES,
        "top_k": args.top_k,
        "candidate_k": args.candidate_k,
        "token_budget": args.token_budget,
        "minimum_answerable_recall": args.minimum_answerable_recall,
        "evaluation_splits": splits,
        "precache_queries": False,
    }
    if args.backend == "postgres":
        if not args.postgres_dsn:
            raise ValueError("PostgreSQL semantic eval requires a DSN")
        baseline = run_postgres_layered_eval(
            repo_root,
            args.dataset.resolve(),
            postgres_dsn=args.postgres_dsn,
            provider=HashingEmbeddingProvider(),
            gate_thresholds=baseline_thresholds,
            **common,
        )
        candidate = run_postgres_layered_eval(
            repo_root,
            args.dataset.resolve(),
            postgres_dsn=args.postgres_dsn,
            provider=provider,
            gate_thresholds=candidate_thresholds,
            **common,
        )
    else:
        baseline = run_sqlite_layered_eval(
            repo_root,
            args.dataset.resolve(),
            provider=HashingEmbeddingProvider(),
            gate_thresholds=baseline_thresholds,
            **common,
        )
        candidate = run_sqlite_layered_eval(
            repo_root,
            args.dataset.resolve(),
            provider=provider,
            gate_thresholds=candidate_thresholds,
            **common,
        )

    comparison = compare_semantic_provider_reports(
        baseline,
        candidate,
        evaluation_split="test" if args.stage == "final" else None,
    )
    policy_source = candidate if selection_candidate is None else selection_candidate
    policy = _policy(policy_source, args.minimum_answerable_recall)
    result = {
        "schema_version": 1,
        "evaluation_id": f"sage-semantic-provider-{args.backend}-{args.stage}-v2",
        "stage": args.stage,
        "protocol": {
            "selection_splits": ["dev", "calibration"],
            "final_splits": ["calibration", "test"],
            "test_used_for_provider_or_gate_selection": False,
            "queries_precached": False,
        },
        "selection_evidence": selection_evidence,
        "baseline": baseline,
        "candidate": candidate,
        "gate_policy": policy.to_dict(),
        "activation": comparison,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "stage": args.stage,
                "output": str(output),
                "provider": candidate["provider"],
                "activation": comparison,
                "gate_policy_id": policy.policy_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if comparison["overall_passed"] else 2


def _build_provider(args: argparse.Namespace, *, api_key: str) -> Any:
    if args.provider == "fastembed":
        return FastEmbedEmbeddingProvider(
            FastEmbedEmbeddingConfig(
                model=DEFAULT_FASTEMBED_MODEL,
                repository=DEFAULT_FASTEMBED_REPOSITORY,
                model_revision=DEFAULT_FASTEMBED_MODEL_REVISION,
                dimensions=DEFAULT_FASTEMBED_DIMENSIONS,
                cache_dir=args.cache_dir,
                batch_size=32,
                local_files_only=args.local_files_only,
            )
        )
    required = (
        api_key,
        args.embedding_base_url,
        args.embedding_model,
        args.embedding_model_revision,
    )
    if any(not str(value).strip() for value in required):
        raise ValueError("cloud embedding provider requires API key, base URL, model, and revision")
    if args.provider == "dashscope":
        return DashScopeEmbeddingProvider(
            DashScopeEmbeddingConfig(
                api_key=api_key,
                base_url=args.embedding_base_url,
                model=args.embedding_model,
                model_revision=args.embedding_model_revision,
                dimensions=args.embedding_dimensions,
                query_instruct=args.query_instruct,
                batch_size=args.embedding_batch_size,
                timeout_seconds=args.embedding_timeout_seconds,
                cost_per_1k_tokens_usd=args.cost_per_1k_tokens_usd,
            )
        )
    if args.provider == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider(
            OpenAICompatibleEmbeddingConfig(
                api_key=api_key,
                base_url=args.embedding_base_url,
                model=args.embedding_model,
                model_revision=args.embedding_model_revision,
                dimensions=args.embedding_dimensions,
                batch_size=args.embedding_batch_size,
                timeout_seconds=args.embedding_timeout_seconds,
                cost_per_1k_tokens_usd=args.cost_per_1k_tokens_usd,
            )
        )
    raise ValueError("unknown semantic evaluation provider")


def _assert_provider_identity(report: dict[str, Any], provider: Any) -> None:
    expected = report.get("provider", {})
    actual = {
        "model_id": provider.model_id,
        "model_revision": provider.model_revision,
        "dimensions": provider.dimensions,
    }
    mismatches = [name for name, value in actual.items() if expected.get(name) != value]
    if mismatches:
        raise ValueError("final provider differs from selection: " + ", ".join(mismatches))


def _thresholds(report: dict[str, Any]) -> dict[KnowledgeRetrievalMode, float]:
    return {mode: float(report["routes"][mode]["gate"]["threshold"]) for mode in _MODES}


def _policy(report: dict[str, Any], minimum_recall: float) -> KnowledgeRelevancePolicy:
    thresholds = _thresholds(report)
    return KnowledgeRelevancePolicy(
        benchmark_id=str(report["dataset"]["dataset_id"]),
        benchmark_revision=str(report["dataset"]["dataset_revision"]),
        corpus_revision=str(report["index"]["corpus_revision"]),
        embedding_model=str(report["provider"]["model_id"]),
        embedding_revision=str(report["provider"]["model_revision"]),
        top_k=int(report["parameters"]["top_k"]),
        min_sparse_score=thresholds["sparse"],
        min_dense_score=thresholds["dense"],
        min_hybrid_score=thresholds["hybrid"],
        minimum_answerable_recall_ratio=minimum_recall,
        schema_version=2,
    )


def _assert_passing_selection(report: dict[str, Any]) -> None:
    if report.get("stage") != "selection":
        raise ValueError("selection report has the wrong stage")
    if not report.get("activation", {}).get("overall_passed"):
        raise ValueError("selection report did not pass semantic activation gates")
    if report.get("candidate", {}).get("parameters", {}).get("evaluation_splits") != [
        "dev",
        "calibration",
    ]:
        raise ValueError("selection report did not preserve the frozen test split")


def _load_report(path: Path) -> dict[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("semantic eval report must be a JSON object")
    return cast(dict[str, Any], payload)


if __name__ == "__main__":
    raise SystemExit(main())
