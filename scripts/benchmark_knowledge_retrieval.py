"""Run the committed Golden Queries against the configured local knowledge index."""

from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core.config.settings import get_settings
from core.knowledge import KnowledgeSourceRoot, KnowledgeStore
from core.knowledge.benchmark import KnowledgeGoldenQuery, evaluate_retrieval


def main() -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=settings.knowledge_workspace_root)
    parser.add_argument("--database", default=settings.knowledge_database_path)
    parser.add_argument("--source-root", default=settings.knowledge_source_root)
    parser.add_argument("--source-id", default=settings.knowledge_source_id)
    parser.add_argument("--source-label", default=settings.knowledge_source_label)
    parser.add_argument("--source-kind", default=settings.knowledge_source_kind)
    parser.add_argument(
        "--golden",
        default=str(Path(__file__).parents[1] / "evals" / "knowledge_golden_queries.json"),
    )
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    for name in ("workspace", "database", "source_root"):
        if not str(getattr(args, name)).strip():
            parser.error(f"--{name.replace('_', '-')} is required")

    store = KnowledgeStore(
        args.workspace,
        args.database,
        {
            args.source_id: KnowledgeSourceRoot(
                root_id=args.source_id,
                kind=args.source_kind,
                label=args.source_label,
                path=Path(args.source_root),
            )
        },
    )
    golden = _load_golden(Path(args.golden))
    ranked_sources: dict[str, tuple[str, ...]] = {}
    latencies: list[float] = []
    for item in golden:
        started = time.perf_counter()
        hits = store.search(item.query, top_k=args.top_k)
        latencies.append((time.perf_counter() - started) * 1_000)
        ranked_sources[item.query_id] = tuple(
            dict.fromkeys(hit.chunk.source_relative_path for hit in hits)
        )
    report = evaluate_retrieval(golden, ranked_sources, top_k=args.top_k)
    result = asdict(report)
    result["p95_latency_ms"] = _percentile(latencies, 0.95)
    result["index"] = asdict(store.index_summary())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _load_golden(path: Path) -> tuple[KnowledgeGoldenQuery, ...]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("knowledge golden query file must contain a list")
    result: list[KnowledgeGoldenQuery] = []
    for raw in payload:
        if not isinstance(raw, dict) or not isinstance(raw.get("relevant_sources"), list):
            raise ValueError("invalid knowledge golden query")
        result.append(
            KnowledgeGoldenQuery(
                query_id=str(raw["id"]),
                query=str(raw["query"]),
                category=str(raw["category"]),
                relevant_sources=tuple(str(item) for item in raw["relevant_sources"]),
            )
        )
    return tuple(result)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return round(ordered[index], 3)


if __name__ == "__main__":
    raise SystemExit(main())
