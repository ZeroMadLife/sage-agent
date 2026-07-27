"""Run the versioned Sage corpus through layered SQLite retrieval baselines."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from core.knowledge.eval_runner import run_sqlite_layered_eval
from core.knowledge.retrieval import KnowledgeRetrievalMode


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=repo_root / "knowledge" / "eval" / "dataset.json",
    )
    parser.add_argument(
        "--mode",
        action="append",
        choices=("sparse", "dense", "hybrid"),
        dest="modes",
        help="Repeat to select routes; defaults to all three baselines.",
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--candidate-k", type=int, default=50)
    parser.add_argument("--token-budget", type=int, default=3_000)
    parser.add_argument("--minimum-answerable-recall", type=float, default=0.90)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Permit exploratory output from a dirty source tree.",
    )
    args = parser.parse_args()

    modes = tuple(
        cast(KnowledgeRetrievalMode, mode)
        for mode in (args.modes or ("sparse", "dense", "hybrid"))
    )
    result = run_sqlite_layered_eval(
        repo_root,
        args.dataset.resolve(),
        retrieval_modes=modes,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        token_budget=args.token_budget,
        minimum_answerable_recall=args.minimum_answerable_recall,
    )
    if result["source"]["dirty"] and not args.allow_dirty:
        raise ValueError("layered eval report must come from a clean source tree")
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
