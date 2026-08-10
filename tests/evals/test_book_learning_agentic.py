from __future__ import annotations

import pytest

from evals.book_learning_agentic import AgenticEvalCase, evaluate_agentic_outcomes


def test_agentic_metrics_separate_support_from_faithfulness_labels() -> None:
    report = evaluate_agentic_outcomes(
        [
            AgenticEvalCase(
                case_id="direct-1",
                expected_decision="answer",
                expected_agentic=False,
                expected_citations=("kcite_a",),
                actual_decision="answer",
                actual_stop_reason="evidence_sufficient",
                actual_citations=("kcite_a",),
                answer_citations=("kcite_a",),
                child_count=0,
                retrieval_rounds=1,
                token_usage=300,
                latency_ms=120,
                faithfulness=True,
                answer_relevance=True,
            ),
            AgenticEvalCase(
                case_id="gap-1",
                expected_decision="abstain",
                expected_agentic=True,
                expected_citations=(),
                actual_decision="answer",
                actual_stop_reason="synthesis_uncited",
                actual_citations=("kcite_b",),
                answer_citations=(),
                child_count=3,
                retrieval_rounds=2,
                token_usage=900,
                latency_ms=350,
            ),
        ]
    )

    metrics = report["metrics"]
    assert metrics["answerable_case_count"] == 1
    assert metrics["unanswerable_case_count"] == 1
    assert metrics["evidence_coverage"] == 1.0
    assert metrics["citation_support"] == 0.5
    assert metrics["false_acceptance_rate"] == 1.0
    assert metrics["faithfulness_pass_rate"] == 1.0
    assert metrics["faithfulness_labeled_count"] == 1
    assert metrics["p95_latency_ms"] == 350
    assert report["stop_reasons"] == {
        "evidence_sufficient": 1,
        "synthesis_uncited": 1,
    }


def test_agentic_metrics_require_nonempty_cases() -> None:
    with pytest.raises(ValueError, match="requires cases"):
        evaluate_agentic_outcomes([])
