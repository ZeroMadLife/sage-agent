#!/usr/bin/env python3
"""Validate the frozen RAG corpus/eval contract without running retrieval."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from core.knowledge.datasets import load_versioned_dataset


def build_summary(repo_root: Path, dataset_path: Path) -> dict[str, object]:
    dataset = load_versioned_dataset(repo_root, dataset_path)
    return {
        "dataset_id": dataset.manifest.dataset_id,
        "dataset_revision": dataset.manifest.dataset_revision,
        "corpus_count": len(dataset.corpus),
        "case_count": len(dataset.cases),
        "split_counts": dataset.split_counts,
        "frozen_test": dataset.manifest.frozen_test,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("knowledge/eval/dataset.json"),
        help="repository-relative dataset manifest",
    )
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    dataset_path = args.dataset if args.dataset.is_absolute() else repo_root / args.dataset
    print(json.dumps(build_summary(repo_root, dataset_path), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
