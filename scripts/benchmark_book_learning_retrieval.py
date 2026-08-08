"""Fetch and run the frozen public-domain long-book retrieval benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from core.knowledge.benchmark_runner import (
    load_embedding_provider,
    load_manifest,
    run_benchmark,
)
from core.knowledge.retrieval import KnowledgeAblationPolicy
from evals.book_learning_claims import (
    ClaimEvidenceEvalCase,
    ClaimEvidenceGoldCase,
    claim_eval_cases_from_report,
    evaluate_claim_evidence,
    load_claim_evidence_gold,
)

_STRATEGIES = (
    "baseline",
    "contextual_chunk",
    "parent_child",
    "described_parent_child",
    "semantic_boundary",
)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repo_root / "evals" / "book_learning_benchmark_v1_manifest.json",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--strategy",
        choices=_STRATEGIES,
        default="baseline",
        help="Run exactly one chunk/index ablation strategy",
    )
    parser.add_argument(
        "--provider-factory",
        help="Optional import path module:attribute returning a DenseEmbeddingProvider",
    )
    parser.add_argument(
        "--claim-gold",
        type=Path,
        default=repo_root / "evals" / "book_learning_claim_gold_v1.jsonl",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--skip-fetch",
        action="store_true",
        help="Require an already verified ignored corpus cache",
    )
    args = parser.parse_args()

    if not args.skip_fetch:
        _fetch_corpus(repo_root)
    manifest = load_manifest(repo_root, args.manifest.resolve())
    provider = load_embedding_provider(args.provider_factory)
    result = run_benchmark(
        repo_root,
        manifest,
        top_k=args.top_k,
        provider=provider,
        ablation_policy=KnowledgeAblationPolicy(strategy=args.strategy),
    )
    result["evaluation_scope"] = {
        "stage": "retrieval",
        "corpus": "real_public_domain_books",
        "human_gold_status": "seed_manual",
        "production_claim_allowed": False,
        "reason": "seed gold must be expanded and independently reviewed before activation",
    }
    observed_claims = claim_eval_cases_from_report(result)
    claim_gold = _select_claim_gold(args.claim_gold, observed_claims)
    claim_report = evaluate_claim_evidence(claim_gold, observed_claims)
    claim_report["gold"] = _claim_gold_receipt(repo_root, args.claim_gold)
    result["claim_evidence"] = claim_report
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


def _fetch_corpus(repo_root: Path) -> None:
    completed = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "fetch_book_learning_corpus.py")],
        cwd=repo_root,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError("book corpus fetch or checksum verification failed")


def _select_claim_gold(
    path: Path, observed: tuple[ClaimEvidenceEvalCase, ...]
) -> tuple[ClaimEvidenceGoldCase, ...]:
    gold_by_id = {case.query_id: case for case in load_claim_evidence_gold(path)}
    try:
        return tuple(gold_by_id[str(case.query_id)] for case in observed)
    except KeyError as exc:
        raise ValueError(f"claim gold is missing benchmark query: {exc.args[0]}") from exc


def _claim_gold_receipt(repo_root: Path, path: Path) -> dict[str, object]:
    resolved = path.resolve()
    try:
        dataset = resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        dataset = str(resolved)
    return {
        "dataset": dataset,
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "review_status": "seed_manual",
        "production_claim_allowed": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
