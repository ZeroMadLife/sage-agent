"""Compare pre-retrieval query rewrite variants from a bounded recovery receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from core.knowledge.benchmark import load_benchmark_v2
from evals.book_learning_claims import load_claim_evidence_gold
from evals.book_learning_query_rewrite import (
    QueryRewriteObservation,
    evaluate_query_rewrite_variants,
    fuse_ranked_passages,
)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery-report", type=Path, required=True)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=repo_root / "evals" / "book_learning_benchmark_v1.jsonl",
    )
    parser.add_argument(
        "--claim-gold",
        type=Path,
        default=repo_root / "evals" / "book_learning_claim_gold_v1.jsonl",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    recovery_path = args.recovery_report.resolve()
    recovery: Any = json.loads(recovery_path.read_text(encoding="utf-8"))
    observations, latency = _observations(recovery, top_k=args.top_k)
    observation_ids = {item.query_id for item in observations}
    queries = tuple(
        item for item in load_benchmark_v2(args.dataset.resolve()) if item.query_id in observation_ids
    )
    claim_gold = tuple(
        item
        for item in load_claim_evidence_gold(args.claim_gold.resolve())
        if item.query_id in observation_ids
    )
    result = evaluate_query_rewrite_variants(
        queries,
        claim_gold,
        observations,
        top_k=args.top_k,
    )
    result.update(
        {
            "source": {
                "recovery_report": _relative_or_absolute(repo_root, recovery_path),
                "recovery_report_sha256": hashlib.sha256(recovery_path.read_bytes()).hexdigest(),
                "embedding_provider": recovery["retrieval_report"]["provider"],
                "benchmark_revision": recovery["retrieval_report"]["benchmark_revision"],
            },
            "latency_ms": latency,
            "evidence_boundaries": {
                "rewrite_status": recovery["protocol"]["rewrite_status"],
                "rewrite_is_not_model_evidence": True,
                "comparison_scope": "rewrite_plan_cases_only",
                "production_traffic": False,
                "online_default_changed": False,
            },
        }
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def _observations(
    recovery: Any,
    *,
    top_k: int,
) -> tuple[tuple[QueryRewriteObservation, ...], dict[str, object]]:
    if not isinstance(recovery, dict) or recovery.get("stage") != "bounded_recovery":
        raise ValueError("query rewrite ablation requires a bounded recovery receipt")
    protocol = recovery.get("protocol")
    if not isinstance(protocol, dict) or protocol.get("rewrite_status") != "oracle_manual":
        raise ValueError("query rewrite ablation currently requires oracle_manual rewrites")
    report = recovery.get("retrieval_report")
    plans = recovery.get("plans")
    if not isinstance(report, dict) or not isinstance(report.get("cases"), list):
        raise ValueError("recovery receipt is missing retrieval cases")
    if not isinstance(plans, list) or not plans:
        raise ValueError("recovery receipt is missing rewrite plans")
    by_id = {str(item["query_id"]): item for item in report["cases"]}
    observations: list[QueryRewriteObservation] = []
    original_latencies: list[float] = []
    expanded_latencies: list[float] = []
    rewrite_request_count = 0
    for plan in plans:
        case_id = str(plan["case_id"])
        original = by_id[case_id]
        rewrite_routes: list[tuple[str, ...]] = []
        rewrite_latencies: list[float] = []
        for index, _query in enumerate(plan["rewrite_queries"], start=1):
            rewrite_case = by_id[f"{case_id}__recovery_{index}"]
            rewrite_routes.append(_passages(rewrite_case))
            rewrite_latencies.append(float(rewrite_case["latency_ms"]))
        original_latency = float(original["latency_ms"])
        original_latencies.append(original_latency)
        expanded_latencies.append(original_latency + max(rewrite_latencies))
        rewrite_request_count += len(rewrite_routes)
        observations.append(
            QueryRewriteObservation(
                query_id=case_id,
                original_passage_ids=_passages(original),
                rewrite_passage_ids=fuse_ranked_passages(
                    tuple(rewrite_routes), top_k=top_k, rank_constant=60
                ),
                rewrite_preserved_intent=bool(plan["rewrite_preserved_intent"]),
            )
        )
    return tuple(observations), {
        "original_only": _latency_summary(original_latencies),
        "original_plus_rewrite_serial_upper_bound": _latency_summary(expanded_latencies),
        "rewrite_request_count": rewrite_request_count,
        "rewrite_requests_per_case": round(rewrite_request_count / len(observations), 4),
        "provider_generation_latency_included": False,
    }


def _passages(case: dict[str, Any]) -> tuple[str, ...]:
    hits = case.get("hits")
    if not isinstance(hits, list):
        raise ValueError("retrieval case is missing hits")
    return tuple(dict.fromkeys(str(hit["passage_id"]) for hit in hits))


def _latency_summary(values: list[float]) -> dict[str, float]:
    return {"p50": _percentile(values, 0.50), "p95": _percentile(values, 0.95)}


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * quantile) - 1)
    return round(ordered[index], 3)


def _relative_or_absolute(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
