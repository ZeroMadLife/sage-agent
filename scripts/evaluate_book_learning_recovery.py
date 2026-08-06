"""Evaluate one bounded, manually audited query-rewrite round on long books."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from core.knowledge.benchmark import (
    KnowledgeBenchmarkQueryV2,
    evaluate_retrieval_v2,
    load_benchmark_v2,
)
from core.knowledge.benchmark_runner import (
    load_embedding_provider,
    load_manifest,
    run_benchmark,
)
from core.knowledge.retrieval import KnowledgeAblationPolicy
from evals.book_learning_stages import RecoveryEvalCase, evaluate_recovery


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repo_root / "evals" / "book_learning_benchmark_v1_manifest.json",
    )
    parser.add_argument(
        "--plans",
        type=Path,
        default=repo_root / "evals" / "book_learning_recovery_v1.jsonl",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--provider-factory",
        help="Optional import path module:attribute returning a DenseEmbeddingProvider",
    )
    parser.add_argument("--strategy", choices=_strategies(), default="contextual_chunk")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Require an already verified ignored corpus cache",
    )
    args = parser.parse_args()

    if not args.skip_fetch:
        raise RuntimeError("recovery runner requires --skip-fetch after corpus verification")
    manifest = load_manifest(repo_root, args.manifest.resolve())
    queries_path = repo_root / manifest.dataset
    source_queries = load_benchmark_v2(queries_path)
    plans = _load_plans(args.plans.resolve(), {query.query_id: query for query in source_queries})
    provider = load_embedding_provider(args.provider_factory)
    policy = KnowledgeAblationPolicy(strategy=args.strategy)

    recovery_records: list[dict[str, Any]] = []
    recovery_ids: dict[str, tuple[str, ...]] = {}
    for plan in plans:
        query = next(item for item in source_queries if item.query_id == plan["case_id"])
        ids: list[str] = []
        for index, rewrite in enumerate(plan["rewrite_queries"]):
            rewrite_id = f"{query.query_id}__recovery_{index + 1}"
            ids.append(rewrite_id)
            recovery_records.append(
                {
                    "id": rewrite_id,
                    "query": rewrite,
                    "category": query.category,
                    "split": query.split,
                    "answerable": query.answerable,
                    "provenance": plan["provenance"],
                    "relevant_passages": [
                        {
                            "source": judgment.document_id.split("#", 1)[0],
                            "section": judgment.document_id.split("#", 1)[1],
                            "relevance": judgment.relevance,
                        }
                        for judgment in query.relevant
                    ],
                    "required_claims": list(query.required_claims),
                    "forbidden_claims": list(query.forbidden_claims),
                }
            )
        recovery_ids[query.query_id] = tuple(ids)

    with tempfile.TemporaryDirectory(
        prefix="sage-book-recovery-", dir=repo_root / ".coding"
    ) as temp:
        temp_path = Path(temp)
        dataset = temp_path / "recovery-dataset.jsonl"
        dataset.write_text(
            "\n".join(
                [
                    json.dumps(_query_record(queries_path, query.query_id), ensure_ascii=False)
                    for query in source_queries
                ]
                + [json.dumps(record, ensure_ascii=False) for record in recovery_records]
            )
            + "\n",
            encoding="utf-8",
        )
        recovery_manifest = replace(
            manifest,
            dataset=dataset.relative_to(repo_root).as_posix(),
            dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
            benchmark_id=f"{manifest.benchmark_id}-recovery",
        )
        report = run_benchmark(
            repo_root,
            recovery_manifest,
            top_k=args.top_k,
            provider=provider,
            ablation_policy=policy,
        )

    by_id = {str(case["query_id"]): case for case in report["cases"]}
    recovery_cases = [
        _to_eval_case(
            next(item for item in source_queries if item.query_id == plan["case_id"]),
            plan,
            by_id,
            recovery_ids[plan["case_id"]],
        )
        for plan in plans
    ]
    first_pass_report = evaluate_retrieval_v2(
        source_queries,
        {
            query.query_id: tuple(
                str(item) for item in by_id[query.query_id]["retrieved_documents"]
            )
            for query in source_queries
        },
        top_k=args.top_k,
    )
    first_pass_latencies = [float(by_id[query.query_id]["latency_ms"]) for query in source_queries]
    result = {
        "schema_version": 1,
        "stage": "bounded_recovery",
        "protocol": {
            "max_recovery_rounds": 1,
            "rewrite_status": "oracle_manual",
            "rewrite_is_not_model_evidence": True,
            "same_index_for_first_and_recovery": True,
            "recovery_latency_uses_parallel_branch_max": True,
            "max_parallel_rewrite_queries": 2,
            "evidence_scope": "candidate_passages_before_bundle_token_budget",
            "decision_policy": "nonempty_retrieval_proxy_without_relevance_gate",
            "claim_coverage_method": "gold_evidence_completeness_proxy",
            "retrieval_report_latency_scope": "first_pass_and_rewrites",
        },
        "first_pass_retrieval": asdict(first_pass_report),
        "first_pass_latency_ms": {
            "p50": _percentile(first_pass_latencies, 0.50),
            "p95": _percentile(first_pass_latencies, 0.95),
        },
        "retrieval_report": report,
        "metrics": evaluate_recovery(recovery_cases),
        "plans": plans,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


def _load_plans(path: Path, queries: dict[str, object]) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw: Any = json.loads(line)
        if (
            not isinstance(raw, dict)
            or set(raw) != {"case_id", "rewrite_queries", "rewrite_preserved_intent", "provenance"}
            or raw["case_id"] not in queries
            or raw["case_id"] in seen_case_ids
            or not isinstance(raw["rewrite_queries"], list)
            or not raw["rewrite_queries"]
            or len(raw["rewrite_queries"]) > 2
            or not all(
                isinstance(item, str) and 0 < len(item.strip()) <= 2_000
                for item in raw["rewrite_queries"]
            )
            or not isinstance(raw["rewrite_preserved_intent"], bool)
            or raw["provenance"] != "oracle_manual_rewrite"
        ):
            raise ValueError(f"invalid recovery plan at line {line_number}")
        seen_case_ids.add(str(raw["case_id"]))
        plans.append(
            {
                "case_id": str(raw["case_id"]),
                "rewrite_queries": [str(item).strip() for item in raw["rewrite_queries"]],
                "rewrite_preserved_intent": bool(raw["rewrite_preserved_intent"]),
                "provenance": str(raw["provenance"]),
            }
        )
    if not plans:
        raise ValueError("recovery plans require at least one case")
    return plans


def _query_record(path: Path, query_id: str) -> dict[str, Any]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record: Any = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError("book benchmark record must be an object")
        if record.get("id") == query_id:
            return record
    raise KeyError(query_id)


def _to_eval_case(
    query: KnowledgeBenchmarkQueryV2,
    plan: dict[str, Any],
    by_id: dict[str, dict[str, Any]],
    rewrite_ids: tuple[str, ...],
) -> RecoveryEvalCase:
    query_id = query.query_id
    gold = tuple(item.document_id for item in query.relevant)
    required_claims = query.required_claims
    first = by_id[query_id]
    first_evidence = _evidence(first)
    rewrite_cases = [by_id[item] for item in rewrite_ids]
    final_evidence = tuple(
        dict.fromkeys(
            first_evidence + tuple(ref for case in rewrite_cases for ref in _evidence(case))
        )
    )
    first_complete = query.answerable and set(gold).issubset(first_evidence)
    final_complete = query.answerable and set(gold).issubset(final_evidence)
    return RecoveryEvalCase(
        case_id=query_id,
        answerable=query.answerable,
        gold_evidence=gold,
        required_claims=required_claims,
        first_round_evidence=first_evidence,
        final_evidence=final_evidence,
        first_round_claims=required_claims if first_complete else (),
        final_claims=required_claims if final_complete else (),
        final_decision="answer" if final_evidence else "abstain",
        first_round_latency_ms=round(float(first["latency_ms"])),
        recovery_latency_ms=max(round(float(case["latency_ms"])) for case in rewrite_cases),
        rewrite_preserved_intent=bool(plan["rewrite_preserved_intent"]),
    )


def _evidence(case: dict[str, Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(hit["passage_id"]) for hit in case["hits"]))


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile + 0.9999) - 1))
    return round(ordered[index], 3)


def _strategies() -> tuple[str, ...]:
    return (
        "baseline",
        "contextual_chunk",
        "parent_child",
        "described_parent_child",
        "semantic_boundary",
    )


if __name__ == "__main__":
    raise SystemExit(main())
