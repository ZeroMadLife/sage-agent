"""Evaluate claim-aware evidence coverage from an existing Sage JSON receipt."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evals.book_learning_claims import (
    ClaimEvidenceGoldCase,
    claim_eval_cases_from_report,
    evaluate_claim_evidence,
    load_claim_evidence_gold,
)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--claim-gold",
        type=Path,
        default=repo_root / "evals" / "book_learning_claim_gold_v1.jsonl",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    raw: Any = json.loads(args.report.read_text(encoding="utf-8"))
    if not isinstance(raw, Mapping):
        raise ValueError("claim evaluation input report must be an object")
    result = evaluate_claim_report(raw, load_claim_evidence_gold(args.claim_gold))
    result["source_report_sha256"] = _sha256(args.report)
    result["claim_gold_sha256"] = _sha256(args.claim_gold)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(encoded, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    return 0


def evaluate_claim_report(
    report: Mapping[str, Any], gold: tuple[ClaimEvidenceGoldCase, ...]
) -> dict[str, object]:
    observed = claim_eval_cases_from_report(report)
    gold_by_id = {case.query_id: case for case in gold}
    try:
        selected_gold = tuple(gold_by_id[case.query_id] for case in observed)
    except KeyError as exc:
        raise ValueError(f"claim gold is missing report query: {exc.args[0]}") from exc
    return evaluate_claim_evidence(selected_gold, observed)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["evaluate_claim_report", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
