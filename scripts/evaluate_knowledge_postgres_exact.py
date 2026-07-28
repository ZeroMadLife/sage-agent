"""Evaluate PostgreSQL exact retrieval against the committed SQLite baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from core.config.settings import get_settings
from core.knowledge.eval_runner import (
    compare_layered_reports,
    run_postgres_layered_eval,
)
from core.knowledge.retrieval import KnowledgeRetrievalMode


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=repo_root / "knowledge" / "eval" / "dataset.json",
    )
    parser.add_argument(
        "--sqlite-report",
        type=Path,
        default=repo_root / "evals" / "reports" / "knowledge_sqlite_layered_v1_2026-07-27.json",
    )
    parser.add_argument(
        "--postgres-dsn",
        default=settings.knowledge_postgres_dsn or settings.postgres_sync_dsn,
        help="Defaults to KNOWLEDGE_POSTGRES_DSN or the POSTGRES_* settings.",
    )
    parser.add_argument(
        "--mode",
        action="append",
        choices=("sparse", "dense", "hybrid"),
        dest="modes",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--candidate-k", type=int, default=50)
    parser.add_argument("--token-budget", type=int, default=3_000)
    parser.add_argument("--minimum-answerable-recall", type=float, default=0.90)
    parser.add_argument("--recall-tolerance", type=float, default=0.02)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    modes = tuple(
        cast(KnowledgeRetrievalMode, mode) for mode in (args.modes or ("sparse", "dense", "hybrid"))
    )
    sqlite_report = _load_report(args.sqlite_report.resolve())
    postgres_report = run_postgres_layered_eval(
        repo_root,
        args.dataset.resolve(),
        postgres_dsn=args.postgres_dsn,
        retrieval_modes=modes,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        token_budget=args.token_budget,
        minimum_answerable_recall=args.minimum_answerable_recall,
    )
    if postgres_report["source"]["dirty"] and not args.allow_dirty:
        raise ValueError("PostgreSQL eval report must come from a clean source tree")
    comparison = compare_layered_reports(
        sqlite_report,
        postgres_report,
        recall_tolerance=args.recall_tolerance,
    )
    result = {
        "schema_version": 1,
        "evaluation_id": "sage-postgres-exact-vs-sqlite-v1",
        "sqlite_baseline": {
            "path": args.sqlite_report.resolve().relative_to(repo_root).as_posix(),
            "sha256": "sha256:" + hashlib.sha256(args.sqlite_report.read_bytes()).hexdigest(),
            "deterministic_digest": sqlite_report["deterministic_digest"],
        },
        "postgres": postgres_report,
        "comparison": comparison,
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0 if comparison["overall_passed"] else 2


def _load_report(path: Path) -> dict[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("SQLite layered report must be a JSON object")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
