"""Run the PostgreSQL exact scale gate and conditionally benchmark HNSW."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from core.config.settings import get_settings
from core.knowledge.scale_benchmark import ScaleBenchmarkConfig, run_scale_benchmark


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--postgres-dsn",
        default=settings.knowledge_postgres_dsn or settings.postgres_sync_dsn,
        help="Defaults to KNOWLEDGE_POSTGRES_DSN or POSTGRES_* settings; never printed.",
    )
    parser.add_argument("--scales", default="1000,10000,100000")
    parser.add_argument("--dimensions", type=int, default=384)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--query-count", type=int, default=32)
    parser.add_argument("--warmup-passes", type=int, default=1)
    parser.add_argument("--measured-passes", type=int, default=2)
    parser.add_argument("--exact-p95-sla-ms", type=float, default=100.0)
    parser.add_argument("--ef-search", default="40,80,120,200")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument(
        "--confirm-ephemeral-write",
        action="store_true",
        help="Required acknowledgement: creates and drops a random UNLOGGED benchmark table.",
    )
    args = parser.parse_args()
    if not args.confirm_ephemeral_write:
        parser.error("--confirm-ephemeral-write is required")
    source = _source_state(repo_root)
    if source["dirty"] and not args.allow_dirty:
        parser.error("scale benchmark evidence requires a clean source tree")
    config = ScaleBenchmarkConfig(
        scales=_integer_tuple(args.scales, label="scales"),
        dimensions=args.dimensions,
        top_k=args.top_k,
        query_count=args.query_count,
        warmup_passes=args.warmup_passes,
        measured_passes=args.measured_passes,
        exact_p95_sla_ms=args.exact_p95_sla_ms,
        ef_search_values=_integer_tuple(args.ef_search, label="ef-search"),
    )
    report = run_scale_benchmark(args.postgres_dsn, config=config)
    report["source"] = source
    report["evidence_boundaries"] = {
        "synthetic_fixture": True,
        "production_traffic": False,
        "production_sla_claim": False,
        "runtime_hnsw_enabled": False,
        "deployment_performed": False,
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded, encoding="utf-8")
    summary = {
        "output": output.relative_to(repo_root).as_posix(),
        "source_commit": source["commit"],
        "exact": [
            {
                "scale": item["scale"],
                "recall_at_10": item["recall_at_10"],
                "p95_ms": item["p95_ms"],
            }
            for item in report["exact"]
        ],
        "hnsw_experiment_ran": report["hnsw_build"]["ran"],
        "decision": report["decision"],
        "ephemeral_cleanup_completed": report["ephemeral_cleanup_completed"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _integer_tuple(raw: str, *, label: str) -> tuple[int, ...]:
    try:
        values = tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must contain comma-separated integers") from exc
    if not values:
        raise argparse.ArgumentTypeError(f"{label} cannot be empty")
    return values


def _source_state(repo_root: Path) -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {"commit": commit, "dirty": bool(status.strip())}


if __name__ == "__main__":
    raise SystemExit(main())
