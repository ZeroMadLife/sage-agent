"""Run the Sage relation-aware Knowledge benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.knowledge.benchmark_runner import load_embedding_provider, load_manifest
from core.knowledge.relation_benchmark import (
    load_relation_benchmark,
    run_relation_benchmark,
)
from core.knowledge.relevance import load_relevance_policy


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repo_root / "evals" / "knowledge_benchmark_v2_manifest.json",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=repo_root / "evals" / "knowledge_relation_benchmark_v1.jsonl",
    )
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--provider-factory")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = load_manifest(repo_root, args.manifest.resolve())
    dataset = args.dataset.resolve()
    report = run_relation_benchmark(
        repo_root,
        manifest,
        load_relation_benchmark(dataset),
        provider=load_embedding_provider(args.provider_factory),
        relevance_policy=load_relevance_policy(args.policy.resolve()),
        dataset_path=dataset,
        top_k=args.top_k,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
