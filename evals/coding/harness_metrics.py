"""Deterministic metrics for durable Chat Harness retrieval telemetry."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RetrievalTelemetrySummary:
    decision_count: int
    retrieve_count: int
    skip_count: int
    completed_count: int
    evidence_hit_count: int
    error_count: int
    citation_count: int
    used_tokens: int
    retrieval_rate: float
    evidence_hit_rate: float
    average_duration_ms: float
    average_used_tokens: float

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)


def summarize_retrieval_telemetry(
    events: Iterable[Mapping[str, Any]],
) -> RetrievalTelemetrySummary:
    """Aggregate timeline envelopes or raw Harness payloads without model calls."""
    decisions: list[Mapping[str, Any]] = []
    completions: list[Mapping[str, Any]] = []
    for event in events:
        payload = event.get("payload")
        record = payload if isinstance(payload, Mapping) else event
        event_type = str(record.get("type", ""))
        if event_type == "retrieval_decision":
            decisions.append(record)
        elif event_type == "retrieval_completed":
            completions.append(record)

    retrieve_count = sum(item.get("decision") == "retrieve" for item in decisions)
    skip_count = sum(item.get("decision") == "skip" for item in decisions)
    evidence_hit_count = sum(_non_negative_int(item.get("citation_count")) > 0 for item in completions)
    error_count = sum(
        str(item.get("status", "")) in {"error", "invalid_result"}
        for item in completions
    )
    citation_count = sum(_non_negative_int(item.get("citation_count")) for item in completions)
    used_tokens = sum(_non_negative_int(item.get("used_tokens")) for item in completions)
    duration_ms = sum(_non_negative_int(item.get("duration_ms")) for item in completions)
    decision_count = len(decisions)
    completed_count = len(completions)
    return RetrievalTelemetrySummary(
        decision_count=decision_count,
        retrieve_count=retrieve_count,
        skip_count=skip_count,
        completed_count=completed_count,
        evidence_hit_count=evidence_hit_count,
        error_count=error_count,
        citation_count=citation_count,
        used_tokens=used_tokens,
        retrieval_rate=_ratio(retrieve_count, decision_count),
        evidence_hit_rate=_ratio(evidence_hit_count, completed_count),
        average_duration_ms=_average(duration_ms, completed_count),
        average_used_tokens=_average(used_tokens, completed_count),
    )


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _average(total: int, count: int) -> float:
    return round(total / count, 2) if count else 0.0
