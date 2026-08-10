"""Build the Sage long-book product scorecard from existing JSON receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evals.book_learning_scorecard import (
    build_book_learning_scorecard,
    compare_retrieval_strategies,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrieval-report", type=Path, required=True)
    parser.add_argument("--generation-report", type=Path, required=True)
    parser.add_argument("--generation-claim-report", type=Path)
    parser.add_argument(
        "--strategy-report",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Repeat for comparable retrieval strategy receipts",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    retrieval = _load_report(args.retrieval_report)
    generation = _load_report(args.generation_report)
    generation_claim = (
        _load_report(args.generation_claim_report)
        if args.generation_claim_report is not None
        else None
    )
    result = build_book_learning_scorecard(
        retrieval_report=retrieval,
        generation_report=generation,
        generation_claim_report=generation_claim,
    )
    inputs = {
        "retrieval_report": _receipt(args.retrieval_report),
        "generation_report": _receipt(args.generation_report),
    }
    if args.generation_claim_report is not None:
        inputs["generation_claim_report"] = _receipt(args.generation_claim_report)

    strategy_paths = _strategy_paths(args.strategy_report)
    if strategy_paths:
        result["strategy_selection"] = compare_retrieval_strategies(
            {name: _load_report(path) for name, path in strategy_paths.items()}
        )
        inputs["strategy_reports"] = {
            name: _receipt(path) for name, path in strategy_paths.items()
        }
    result["inputs"] = inputs
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    return 0


def _load_report(path: Path) -> Mapping[str, Any]:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError(f"scorecard input must be a JSON object: {path}")
    return raw


def _strategy_paths(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        name, separator, raw_path = value.partition("=")
        if not separator or not name.strip() or not raw_path.strip():
            raise ValueError("strategy report must use NAME=PATH")
        if name in result:
            raise ValueError(f"duplicate strategy report: {name}")
        result[name] = Path(raw_path)
    if result and len(result) < 2:
        raise ValueError("strategy comparison requires at least two reports")
    return result


def _receipt(path: Path) -> dict[str, str]:
    resolved = path.resolve()
    return {
        "path": str(resolved),
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
