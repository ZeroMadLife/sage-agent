from evals.book_learning_claims import ClaimEvidenceGoldCase, ClaimEvidenceRequirement
from scripts.evaluate_book_learning_claims import evaluate_claim_report


def test_existing_generation_receipt_can_be_scored_without_calling_a_model() -> None:
    gold = (
        ClaimEvidenceGoldCase(
            query_id="q1",
            answerable=True,
            expected_decision="answer",
            claims=(
                ClaimEvidenceRequirement(
                    claim_id="c1",
                    statement="事实",
                    passage_ids=("book#a",),
                    coverage_mode="any",
                ),
            ),
        ),
    )
    report = evaluate_claim_report(
        {
            "stage": "llmwiki_generation",
            "cases": [
                {
                    "query_id": "q1",
                    "first_pass_evidence": [],
                    "final_evidence": [{"passage_id": "book#a"}],
                    "accepted_decision": "answer",
                }
            ],
        },
        gold,
    )

    assert report["metrics"]["first_pass_claim_evidence_coverage"] == 0.0
    assert report["metrics"]["claim_evidence_coverage"] == 1.0
    assert report["metrics"]["claim_recovery_gain"] == 1.0
    assert report["metrics"]["answer_readiness_precision"] == 1.0
