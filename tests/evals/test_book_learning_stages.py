from evals.book_learning_stages import (
    GenerationEvalCase,
    RecoveryEvalCase,
    evaluate_generation,
    evaluate_recovery,
)


def test_recovery_metrics_separate_first_pass_gain_drift_and_abstention() -> None:
    report = evaluate_recovery(
        (
            RecoveryEvalCase(
                case_id="recover",
                answerable=True,
                gold_evidence=("a", "b"),
                required_claims=("definition", "comparison"),
                first_round_evidence=("a",),
                final_evidence=("a", "b"),
                first_round_claims=("definition",),
                final_claims=("definition", "comparison"),
                final_decision="answer",
                first_round_latency_ms=100,
                recovery_latency_ms=220,
                rewrite_preserved_intent=True,
            ),
            RecoveryEvalCase(
                case_id="unknown",
                answerable=False,
                gold_evidence=(),
                required_claims=(),
                first_round_evidence=("noise",),
                final_evidence=("noise",),
                first_round_claims=(),
                final_claims=(),
                final_decision="abstain",
                first_round_latency_ms=80,
                recovery_latency_ms=120,
                rewrite_preserved_intent=False,
            ),
        )
    )

    metrics = report["metrics"]
    assert report["protocol"]["claim_coverage_method"] == (
        "explicit_receipt_or_gold_evidence_proxy"
    )
    assert metrics["answerable_case_count"] == 1
    assert metrics["unanswerable_case_count"] == 1
    assert metrics["first_pass_evidence_recall"] == 0.5
    assert metrics["final_evidence_recall"] == 1.0
    assert metrics["incremental_evidence_recall_gain"] == 0.5
    assert metrics["recovery_success_rate"] == 1.0
    assert metrics["no_new_evidence_rate"] == 0.5
    assert metrics["query_drift_rate"] == 0.5
    assert metrics["false_acceptance_rate"] == 0.0
    assert metrics["correct_abstention_rate"] == 1.0


def test_generation_metrics_keep_ragas_auxiliary_to_claim_and_citation_gates() -> None:
    report = evaluate_generation(
        (
            GenerationEvalCase(
                case_id="answer",
                answerable=True,
                final_decision="answer",
                required_claims=("a", "b"),
                present_claims=("a", "b"),
                unsupported_claims=("b",),
                answer_citations=("c1", "c2"),
                supported_citations=("c1",),
                faithfulness=0.75,
                answer_relevance=0.9,
            ),
            GenerationEvalCase(
                case_id="abstain",
                answerable=False,
                final_decision="abstain",
                required_claims=(),
                present_claims=(),
                unsupported_claims=(),
                answer_citations=(),
                supported_citations=(),
            ),
        )
    )

    assert report["protocol"]["ragas_is_auxiliary"] is True
    assert report["metrics"] == {
        "case_count": 2,
        "claim_coverage": 1.0,
        "unsupported_claim_rate": 0.25,
        "citation_correctness": 0.75,
        "false_acceptance_rate": 0.0,
        "faithfulness": 0.75,
        "answer_relevance": 0.9,
        "faithfulness_labeled_count": 1,
        "answer_relevance_labeled_count": 1,
    }


def test_false_acceptance_rate_uses_only_unanswerable_cases_as_denominator() -> None:
    recovery = evaluate_recovery(
        (
            RecoveryEvalCase(
                case_id="answerable",
                answerable=True,
                gold_evidence=("a",),
                required_claims=("a",),
                first_round_evidence=("a",),
                final_evidence=("a",),
                first_round_claims=("a",),
                final_claims=("a",),
                final_decision="answer",
                first_round_latency_ms=1,
                recovery_latency_ms=1,
            ),
            RecoveryEvalCase(
                case_id="false-acceptance",
                answerable=False,
                gold_evidence=(),
                required_claims=(),
                first_round_evidence=("noise",),
                final_evidence=("noise",),
                first_round_claims=(),
                final_claims=(),
                final_decision="answer",
                first_round_latency_ms=1,
                recovery_latency_ms=1,
            ),
        )
    )
    generation = evaluate_generation(
        (
            GenerationEvalCase(
                case_id="answerable",
                answerable=True,
                final_decision="answer",
                required_claims=("a",),
                present_claims=("a",),
                unsupported_claims=(),
                answer_citations=("c",),
                supported_citations=("c",),
            ),
            GenerationEvalCase(
                case_id="false-acceptance",
                answerable=False,
                final_decision="answer",
                required_claims=(),
                present_claims=(),
                unsupported_claims=(),
                answer_citations=(),
                supported_citations=(),
            ),
        )
    )

    assert recovery["metrics"]["false_acceptance_rate"] == 1.0
    assert recovery["metrics"]["first_pass_evidence_recall"] == 1.0
    assert generation["metrics"]["false_acceptance_rate"] == 1.0
