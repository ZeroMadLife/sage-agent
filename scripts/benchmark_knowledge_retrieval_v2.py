"""Run the frozen Sage Knowledge Benchmark v2 and emit a machine-readable report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.knowledge.benchmark_runner import (
    load_embedding_provider,
    load_manifest,
    run_benchmark,
)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repo_root / "evals" / "knowledge_benchmark_v2_manifest.json",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--provider-factory",
        help="Optional import path module:attribute returning a DenseEmbeddingProvider",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest = load_manifest(repo_root, args.manifest.resolve())
    provider = load_embedding_provider(args.provider_factory)
    result = run_benchmark(repo_root, manifest, top_k=args.top_k, provider=provider)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
