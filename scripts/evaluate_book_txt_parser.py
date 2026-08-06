#!/usr/bin/env python3
"""Evaluate deterministic TXT parsing on the versioned local book corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

from core.knowledge.parsing import ParseRequest, TxtParser

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_MANIFEST = _ROOT / "evals/corpora/book_learning_public_domain_v1.json"
_DEFAULT_CORPUS = _ROOT / ".coding/corpora/book-learning-public-domain-v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    parser.add_argument("--corpus-dir", type=Path, default=_DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    reports = [_evaluate(document, args.corpus_dir) for document in manifest["documents"]]
    report = {
        "dataset_id": manifest["dataset_id"],
        "dataset_revision": manifest["revision"],
        "document_count": len(reports),
        "total_size_bytes": sum(item["size_bytes"] for item in reports),
        "total_block_count": sum(item["block_count"] for item in reports),
        "total_heading_count": sum(item["heading_count"] for item in reports),
        "locator_coverage": _weighted_ratio(reports, "located_block_count", "block_count"),
        "nonempty_line_retention": _weighted_ratio(
            reports, "retained_nonempty_line_count", "nonempty_line_count"
        ),
        "documents": reports,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


def _evaluate(document: dict[str, Any], corpus_dir: Path) -> dict[str, Any]:
    path = corpus_dir / str(document["filename"])
    payload = path.read_bytes()
    actual_sha = hashlib.sha256(payload).hexdigest()
    if actual_sha != document["expected_sha256"]:
        raise ValueError(f"corpus checksum mismatch for {document['document_id']}")
    started = time.perf_counter()
    parsed = TxtParser().parse(
        ParseRequest(
            source_id=str(document["document_id"]),
            relative_path=str(document["filename"]),
            source_revision=f"sha256:{actual_sha}",
            media_type="text/plain",
            payload=payload,
        )
    )
    latency_ms = round((time.perf_counter() - started) * 1_000, 3)
    covered_lines: set[int] = set()
    located = 0
    locator_round_trip = 0
    for block in parsed.blocks:
        if block.line_start is None or block.line_end is None:
            continue
        if block.byte_start is None or block.byte_end is None:
            continue
        located += 1
        covered_lines.update(range(block.line_start, block.line_end + 1))
        raw = payload[block.byte_start : block.byte_end].decode("utf-8")
        if _normalize_block_text(raw) == block.text:
            locator_round_trip += 1
    source_lines = parsed.rendered_markdown.splitlines()
    nonempty_lines = {index for index, line in enumerate(source_lines, start=1) if line.strip()}
    retained = len(nonempty_lines.intersection(covered_lines))
    return {
        "document_id": document["document_id"],
        "language": parsed.language,
        "size_bytes": len(payload),
        "block_count": len(parsed.blocks),
        "heading_count": sum(block.kind == "heading" for block in parsed.blocks),
        "located_block_count": located,
        "locator_round_trip_count": locator_round_trip,
        "nonempty_line_count": len(nonempty_lines),
        "retained_nonempty_line_count": retained,
        "parse_latency_ms": latency_ms,
    }


def _normalize_block_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def _weighted_ratio(items: list[dict[str, Any]], numerator: str, denominator: str) -> float:
    total = sum(int(item[denominator]) for item in items)
    if total == 0:
        return 0.0
    return round(sum(int(item[numerator]) for item in items) / total, 6)


if __name__ == "__main__":
    raise SystemExit(main())
