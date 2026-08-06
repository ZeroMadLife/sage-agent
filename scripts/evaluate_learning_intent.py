"""Evaluate the production learning intent route against the seed-stage dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from evals.learning_intent import evaluate_learning_intent, load_learning_intent_cases


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=repo_root / "evals" / "book_learning_intent_v1_seed.jsonl",
    )
    parser.add_argument("--output", type=Path, required=True)
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
        raise ValueError("learning intent eval requires a clean source tree")
    output = args.output.expanduser().resolve()
    if output.exists():
        raise ValueError("refusing to overwrite an existing learning intent report")
    dataset = args.dataset.expanduser().resolve()
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    report = build_report(
        dataset,
        source_commit=commit,
        source_dirty=dirty,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output),
                "metrics": report["metrics"],
                "failures": report["failures"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_report(
    dataset: Path,
    *,
    source_commit: str,
    source_dirty: bool,
) -> dict[str, Any]:
    cases = load_learning_intent_cases(dataset)
    report = evaluate_learning_intent(cases)
    return {
        **report,
        "evaluation_id": "sage-book-learning-intent-product-seed-v1",
        "source": {"commit": source_commit, "dirty": source_dirty},
        "dataset": {
            "path": dataset.name,
            "sha256": "sha256:" + hashlib.sha256(dataset.read_bytes()).hexdigest(),
            "case_count": len(cases),
            "split_counts": {
                split: sum(case.dataset_split == split for case in cases)
                for split in ("dev", "calibration", "test")
            },
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
