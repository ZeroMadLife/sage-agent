"""Offline evaluation helpers for Phase 4."""

import json
from pathlib import Path
from typing import Any


def load_cases(path: str) -> list[dict[str, Any]]:
    """Load JSONL travel eval cases."""
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def summarize_results(results: list[dict[str, Any]]) -> dict[str, float]:
    """Compute deterministic eval metrics."""
    total = len(results)
    if total == 0:
        return {"schema_valid_rate": 0.0, "verifier_pass_rate": 0.0, "p95_latency_ms": 0.0}

    latencies = sorted(int(item.get("latency_ms", 0)) for item in results)
    p95_index = min(total - 1, int(total * 0.95))
    return {
        "schema_valid_rate": sum(1 for item in results if item.get("schema_valid")) / total,
        "verifier_pass_rate": sum(1 for item in results if item.get("verifier_passed")) / total,
        "p95_latency_ms": float(latencies[p95_index]),
    }
