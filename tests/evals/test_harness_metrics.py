from __future__ import annotations

from evals.coding.harness_metrics import summarize_retrieval_telemetry


def test_summarizes_retrieval_gate_latency_tokens_and_evidence_hits() -> None:
    summary = summarize_retrieval_telemetry([
        {"payload": {"type": "retrieval_decision", "decision": "retrieve"}},
        {"payload": {
            "type": "retrieval_completed",
            "status": "evidence_found",
            "citation_count": 3,
            "used_tokens": 420,
            "duration_ms": 120,
        }},
        {"payload": {"type": "retrieval_decision", "decision": "skip"}},
        {"payload": {"type": "retrieval_decision", "decision": "retrieve"}},
        {
            "type": "retrieval_completed",
            "status": "invalid_result",
            "citation_count": -4,
            "used_tokens": "unknown",
            "duration_ms": 80,
        },
    ])

    assert summary.to_dict() == {
        "decision_count": 3,
        "retrieve_count": 2,
        "skip_count": 1,
        "completed_count": 2,
        "evidence_hit_count": 1,
        "error_count": 1,
        "citation_count": 3,
        "used_tokens": 420,
        "retrieval_rate": 0.6667,
        "evidence_hit_rate": 0.5,
        "average_duration_ms": 100.0,
        "average_used_tokens": 210.0,
    }


def test_empty_retrieval_telemetry_has_stable_zero_metrics() -> None:
    summary = summarize_retrieval_telemetry([])

    assert summary.decision_count == 0
    assert summary.retrieval_rate == 0.0
    assert summary.evidence_hit_rate == 0.0
    assert summary.average_duration_ms == 0.0
    assert summary.average_used_tokens == 0.0
