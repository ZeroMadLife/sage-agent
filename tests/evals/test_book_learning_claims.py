from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.knowledge.benchmark import load_benchmark_v2
from evals.book_learning_claims import (
    ClaimEvidenceEvalCase,
    ClaimEvidenceGoldCase,
    ClaimEvidenceRequirement,
    claim_eval_cases_from_report,
    evaluate_claim_evidence,
    load_claim_evidence_gold,
    missing_claim_brief,
)


def _gold() -> tuple[ClaimEvidenceGoldCase, ...]:
    return (
        ClaimEvidenceGoldCase(
            query_id="answerable-recovery",
            answerable=True,
            expected_decision="answer",
            claims=(
                ClaimEvidenceRequirement(
                    claim_id="c-any",
                    statement="任一候选段落都能支持该事实",
                    passage_ids=("book#a", "book#alternative"),
                    coverage_mode="any",
                ),
                ClaimEvidenceRequirement(
                    claim_id="c-all",
                    statement="该事实需要两段证据共同支持",
                    passage_ids=("book#b", "book#c"),
                    coverage_mode="all",
                ),
            ),
        ),
        ClaimEvidenceGoldCase(
            query_id="answerable-incomplete",
            answerable=True,
            expected_decision="answer",
            claims=(
                ClaimEvidenceRequirement(
                    claim_id="c-missing",
                    statement="缺失的必要事实",
                    passage_ids=("book#missing",),
                    coverage_mode="any",
                ),
            ),
        ),
        ClaimEvidenceGoldCase(
            query_id="unanswerable",
            answerable=False,
            expected_decision="abstain",
            claims=(),
        ),
    )


def test_claim_evidence_metrics_separate_coverage_recovery_and_gate_quality() -> None:
    report = evaluate_claim_evidence(
        _gold(),
        (
            ClaimEvidenceEvalCase(
                query_id="answerable-recovery",
                first_round_passage_ids=("book#a", "book#b"),
                final_passage_ids=("book#a", "book#b", "book#c"),
                final_decision="answer",
            ),
            ClaimEvidenceEvalCase(
                query_id="answerable-incomplete",
                first_round_passage_ids=(),
                final_passage_ids=("noise#similar",),
                final_decision="answer",
            ),
            ClaimEvidenceEvalCase(
                query_id="unanswerable",
                first_round_passage_ids=("noise#similar",),
                final_passage_ids=("noise#similar",),
                final_decision="answer",
            ),
        ),
    )

    assert report["protocol"] == {
        "coverage_unit": "required_claim",
        "passage_matching": "exact_id",
        "answerable_requires_all_claims": True,
        "unanswerable_never_becomes_complete_from_similar_evidence": True,
        "online_gate_activated": False,
        "chain_of_thought_required": False,
    }
    assert report["metrics"] == {
        "case_count": 3,
        "evaluated_case_count": 3,
        "provider_failure_count": 0,
        "answerable_case_count": 2,
        "unanswerable_case_count": 1,
        "required_claim_count": 3,
        "first_pass_claim_evidence_coverage": 0.25,
        "claim_evidence_coverage": 0.5,
        "bundle_completeness_rate": 0.5,
        "claim_recovery_gain": 0.25,
        "recovery_resolution_rate": 0.5,
        "answer_readiness_precision": 0.3333,
        "answer_readiness_recall": 1.0,
        "insufficient_acceptance_rate": 0.6667,
        "false_acceptance_rate": 1.0,
        "correct_abstention_rate": 0.0,
    }
    cases = {case["query_id"]: case for case in report["cases"]}
    assert cases["answerable-recovery"]["first_round_covered_claim_ids"] == ["c-any"]
    assert cases["answerable-recovery"]["final_covered_claim_ids"] == ["c-any", "c-all"]
    assert cases["answerable-incomplete"]["missing_claim_ids"] == ["c-missing"]
    assert cases["unanswerable"]["evidence_complete"] is False


def test_provider_failures_are_not_folded_into_claim_or_gate_quality() -> None:
    report = evaluate_claim_evidence(
        (_gold()[0],),
        (
            ClaimEvidenceEvalCase(
                query_id="answerable-recovery",
                first_round_passage_ids=(),
                final_passage_ids=(),
                final_decision=None,
                evaluation_status="provider_error",
            ),
        ),
    )

    assert report["metrics"]["evaluated_case_count"] == 0
    assert report["metrics"]["provider_failure_count"] == 1
    assert report["metrics"]["claim_evidence_coverage"] is None
    assert report["metrics"]["answer_readiness_precision"] is None


def test_missing_claim_brief_is_atomic_and_never_contains_passage_ids() -> None:
    gold = _gold()[0]

    brief = missing_claim_brief(gold, ("book#a", "book#b"))

    assert brief == ({"claim_id": "c-all", "statement": "该事实需要两段证据共同支持"},)
    assert all("passage_ids" not in item for item in brief)
    assert missing_claim_brief(_gold()[2], ("noise#similar",)) == ()


def test_report_adapter_keeps_retrieval_and_generation_rounds_distinct() -> None:
    retrieval = claim_eval_cases_from_report(
        {
            "cases": (
                {
                    "query_id": "q1",
                    "retrieved_documents": ("book#a", "book#b", "book#a"),
                },
            )
        }
    )
    generation = claim_eval_cases_from_report(
        {
            "stage": "llmwiki_generation",
            "cases": [
                {
                    "query_id": "q1",
                    "first_pass_evidence": [{"passage_id": "book#a"}],
                    "final_evidence": [
                        {"passage_id": "book#a"},
                        {"passage_id": "book#b"},
                    ],
                    "accepted_decision": "answer",
                },
                {
                    "query_id": "q2",
                    "first_pass_evidence": [],
                    "final_evidence": [],
                    "accepted_decision": "abstain",
                    "failure": {"stage": "planner", "error_type": "timeout"},
                },
            ],
        }
    )

    assert retrieval == (
        ClaimEvidenceEvalCase(
            query_id="q1",
            first_round_passage_ids=("book#a", "book#b"),
            final_passage_ids=("book#a", "book#b"),
            final_decision=None,
        ),
    )
    retrieval_metrics = evaluate_claim_evidence(
        (
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
        ),
        retrieval,
    )["metrics"]
    assert retrieval_metrics["answer_readiness_precision"] is None
    assert retrieval_metrics["answer_readiness_recall"] is None
    assert retrieval_metrics["insufficient_acceptance_rate"] is None
    assert generation[0].first_round_passage_ids == ("book#a",)
    assert generation[0].final_passage_ids == ("book#a", "book#b")
    assert generation[0].final_decision == "answer"
    assert generation[1].evaluation_status == "provider_error"
    assert generation[1].final_decision is None


def test_claim_gold_loader_is_strict_and_line_addressable(tmp_path: Path) -> None:
    valid = {
        "query_id": "q1",
        "answerable": True,
        "expected_decision": "answer",
        "claims": [
            {
                "claim_id": "c1",
                "statement": "事实",
                "passage_ids": ["book#a"],
                "coverage_mode": "any",
            }
        ],
    }
    path = tmp_path / "gold.jsonl"
    path.write_text(json.dumps(valid, ensure_ascii=False) + "\n", encoding="utf-8")

    assert load_claim_evidence_gold(path)[0].claims[0].claim_id == "c1"

    invalid = {**valid, "unexpected": True}
    path.write_text(json.dumps(invalid, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        load_claim_evidence_gold(path)

    invalid_type = {
        **valid,
        "claims": [{**valid["claims"][0], "passage_ids": [123]}],
    }
    path.write_text(json.dumps(invalid_type, ensure_ascii=False) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        load_claim_evidence_gold(path)


def test_claim_contract_rejects_vacuous_answerable_and_unanswerable_claims() -> None:
    with pytest.raises(ValueError, match="answerable.*claim"):
        ClaimEvidenceGoldCase(
            query_id="answerable",
            answerable=True,
            expected_decision="answer",
            claims=(),
        )
    with pytest.raises(ValueError, match="unanswerable.*claim"):
        ClaimEvidenceGoldCase(
            query_id="unanswerable",
            answerable=False,
            expected_decision="abstain",
            claims=(
                ClaimEvidenceRequirement(
                    claim_id="c1",
                    statement="不应存在",
                    passage_ids=("book#a",),
                    coverage_mode="any",
                ),
            ),
        )


def test_committed_gold_splits_multi_document_questions_into_atomic_claims() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    gold = {
        case.query_id: case
        for case in load_claim_evidence_gold(repo_root / "evals/book_learning_claim_gold_v1.jsonl")
    }

    assert len(gold) == 14
    assert [claim.claim_id for claim in gold["book-zh-003"].claims] == [
        "wukong_join",
        "bajie_join",
    ]
    assert gold["book-zh-003"].claims[1].coverage_mode == "all"
    assert len(gold["book-cross-001"].claims) == 3
    assert gold["book-cross-001"].claims[2].coverage_mode == "all"
    assert gold["book-hard-001"].claims == ()

    benchmark = {
        case.query_id: case
        for case in load_benchmark_v2(repo_root / "evals/book_learning_benchmark_v1.jsonl")
    }
    assert set(gold) == set(benchmark)
    for query_id, gold_case in gold.items():
        benchmark_case = benchmark[query_id]
        assert gold_case.answerable is benchmark_case.answerable
        relevant_passages = {item.document_id for item in benchmark_case.relevant}
        assert {
            passage_id for claim in gold_case.claims for passage_id in claim.passage_ids
        }.issubset(relevant_passages)
