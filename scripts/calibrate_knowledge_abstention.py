"""Calibrate a version-bound Knowledge relevance policy from Benchmark v2 raw hits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from core.knowledge.benchmark import load_benchmark_v2
from core.knowledge.benchmark_runner import load_manifest
from core.knowledge.relevance_calibration import (
    calibrate_relevance_policy,
    calibration_result_dict,
    report_hits,
)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repo_root / "evals" / "knowledge_benchmark_v2_manifest.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--policy-output", type=Path)
    parser.add_argument("--minimum-answerable-recall-ratio", type=float, default=0.9)
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    manifest = load_manifest(repo_root, args.manifest.resolve())
    raw: Any = json.loads(args.report.resolve().read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("benchmark report must be an object")
    if raw.get("benchmark_id") != manifest.benchmark_id:
        raise ValueError("benchmark report id does not match manifest")
    if raw.get("benchmark_revision") != manifest.benchmark_revision:
        raise ValueError("benchmark report revision does not match manifest")
    if raw.get("dataset_sha256") != manifest.dataset_sha256:
        raise ValueError("benchmark report dataset does not match manifest")
    if bool(raw.get("source_dirty")) and not args.allow_dirty:
        raise ValueError("benchmark report must come from a clean source tree")
    if int(raw.get("top_k", 0)) < 1:
        raise ValueError("benchmark report top_k is invalid")
    provider = raw.get("provider")
    if not isinstance(provider, dict):
        raise ValueError("benchmark report provider metadata is required")
    if not isinstance(provider.get("supports_semantic_recall"), bool):
        raise ValueError("benchmark report semantic provider flag must be boolean")
    index = raw.get("index")
    if not isinstance(index, dict) or not str(index.get("corpus_revision", "")):
        raise ValueError("benchmark report corpus revision is required")

    result = calibrate_relevance_policy(
        load_benchmark_v2((repo_root / manifest.dataset).resolve()),
        report_hits(raw),
        benchmark_id=manifest.benchmark_id,
        benchmark_revision=manifest.benchmark_revision,
        corpus_revision=str(index["corpus_revision"]),
        embedding_model=str(provider.get("model_id", "")),
        embedding_revision=str(provider.get("model_revision", "")),
        supports_semantic_recall=bool(provider.get("supports_semantic_recall", False)),
        top_k=int(raw["top_k"]),
        minimum_answerable_recall_ratio=args.minimum_answerable_recall_ratio,
    )
    payload = calibration_result_dict(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.policy_output is not None:
        args.policy_output.parent.mkdir(parents=True, exist_ok=True)
        args.policy_output.write_text(
            json.dumps(result.policy.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
