"""Evaluate bounded retrieval recovery without test-set tuning or Gate relaxation."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from core.config.settings import get_settings
from core.knowledge.eval_runner import (
    EvalSplit,
    compare_bounded_recovery_reports,
    run_postgres_layered_eval,
    run_sqlite_layered_eval,
)
from core.knowledge.recovery import KnowledgeRecoveryPolicy, TechnicalGlossaryQueryRewriter
from core.knowledge.retrieval import KnowledgeRetrievalMode

_ROUTE: KnowledgeRetrievalMode = "hybrid"


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("selection", "final"), required=True)
    parser.add_argument("--backend", choices=("sqlite", "postgres"), default="postgres")
    parser.add_argument(
        "--dataset",
        type=Path,
        default=repo_root / "knowledge" / "eval" / "dataset.json",
    )
    parser.add_argument(
        "--postgres-dsn",
        default=settings.knowledge_postgres_dsn or settings.postgres_sync_dsn,
    )
    parser.add_argument("--selection-report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--candidate-k", type=int, default=50)
    parser.add_argument("--token-budget", type=int, default=3_000)
    parser.add_argument("--minimum-answerable-recall", type=float, default=0.90)
    parser.add_argument("--maximum-p95-latency-ms", type=float, default=100.0)
    parser.add_argument("--allow-dirty", action="store_true")
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
        raise ValueError("recovery eval report must come from a clean source tree")
    output = args.output.expanduser().resolve()
    if args.stage == "final" and output.exists():
        raise ValueError("refusing to overwrite a frozen-test final report")

    splits: tuple[EvalSplit, ...]
    thresholds: dict[KnowledgeRetrievalMode, float] | None = None
    selection_evidence: dict[str, str] | None = None
    if args.stage == "selection":
        splits = ("dev", "calibration")
    else:
        splits = ("calibration", "test")
        if args.selection_report is None:
            raise ValueError("final stage requires --selection-report")
        selection_path = args.selection_report.expanduser().resolve()
        selection = _load_report(selection_path)
        _assert_passing_selection(selection)
        thresholds = _thresholds(selection["baseline"])
        selection_evidence = {
            "path": selection_path.relative_to(repo_root).as_posix(),
            "sha256": "sha256:" + hashlib.sha256(selection_path.read_bytes()).hexdigest(),
        }

    common: dict[str, Any] = {
        "retrieval_modes": (_ROUTE,),
        "top_k": args.top_k,
        "candidate_k": args.candidate_k,
        "token_budget": args.token_budget,
        "minimum_answerable_recall": args.minimum_answerable_recall,
        "evaluation_splits": splits,
        "precache_queries": False,
    }
    runner: Any = run_sqlite_layered_eval
    runner_kwargs: dict[str, Any] = {}
    if args.backend == "postgres":
        if not args.postgres_dsn:
            raise ValueError("PostgreSQL recovery eval requires a DSN")
        runner = run_postgres_layered_eval
        runner_kwargs["postgres_dsn"] = args.postgres_dsn
    baseline = runner(
        repo_root,
        args.dataset.resolve(),
        gate_thresholds=thresholds,
        **common,
        **runner_kwargs,
    )
    if thresholds is None:
        thresholds = _thresholds(baseline)
    recovery_policy = KnowledgeRecoveryPolicy(enabled=True, max_top_k=args.candidate_k)
    candidate = runner(
        repo_root,
        args.dataset.resolve(),
        gate_thresholds=thresholds,
        recovery_policy=recovery_policy,
        **common,
        **runner_kwargs,
    )
    comparison = compare_bounded_recovery_reports(
        baseline,
        candidate,
        maximum_p95_latency_ms=args.maximum_p95_latency_ms,
    )
    rewriter = TechnicalGlossaryQueryRewriter()
    result = {
        "schema_version": 1,
        "evaluation_id": f"sage-bounded-recovery-{args.backend}-{args.stage}-v1",
        "stage": args.stage,
        "protocol": {
            "selection_splits": ["dev", "calibration"],
            "final_splits": ["calibration", "test"],
            "test_used_for_policy_or_gate_selection": False,
            "gate_threshold_relaxed": False,
            "maximum_rounds": 2,
            "external_web_fallback": False,
        },
        "selection_evidence": selection_evidence,
        "rewriter": {
            "id": rewriter.rewriter_id,
            "revision": rewriter.rewriter_revision,
            "kind": "deterministic_bilingual_technical_glossary",
            "llm_used": False,
        },
        "baseline": baseline,
        "candidate": candidate,
        "activation": comparison,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "stage": args.stage,
                "output": str(output),
                "activation": comparison,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if comparison["overall_passed"] else 2


def _thresholds(report: dict[str, Any]) -> dict[KnowledgeRetrievalMode, float]:
    return {_ROUTE: float(report["routes"][_ROUTE]["gate"]["threshold"])}


def _load_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("recovery report must be a JSON object")
    return payload


def _assert_passing_selection(report: dict[str, Any]) -> None:
    if report.get("stage") != "selection":
        raise ValueError("selection report has the wrong stage")
    if not report.get("activation", {}).get("overall_passed"):
        raise ValueError("selection report did not pass bounded recovery gates")
    expected = ["dev", "calibration"]
    if report.get("baseline", {}).get("parameters", {}).get("evaluation_splits") != expected:
        raise ValueError("selection report used the wrong baseline splits")
    if report.get("candidate", {}).get("parameters", {}).get("evaluation_splits") != expected:
        raise ValueError("selection report used the wrong candidate splits")


if __name__ == "__main__":
    raise SystemExit(main())
