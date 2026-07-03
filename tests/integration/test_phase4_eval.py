"""Phase 4 eval dataset tests."""

from evals.run_eval import load_cases, summarize_results


def test_load_cases_reads_jsonl() -> None:
    cases = load_cases("evals/travel_cases.jsonl")

    assert len(cases) >= 20
    assert {"id", "input", "expected"} <= set(cases[0])


def test_summarize_results_computes_rates() -> None:
    summary = summarize_results(
        [
            {"schema_valid": True, "verifier_passed": True, "latency_ms": 1000},
            {"schema_valid": True, "verifier_passed": False, "latency_ms": 3000},
        ]
    )

    assert summary["schema_valid_rate"] == 1.0
    assert summary["verifier_pass_rate"] == 0.5
    assert summary["p95_latency_ms"] == 3000
