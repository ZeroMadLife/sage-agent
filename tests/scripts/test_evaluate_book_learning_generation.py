from __future__ import annotations

import pytest

from scripts.evaluate_book_learning_generation import (
    _accepted_decision,
    _parse_json_object,
    _rewrite_queries,
)


def test_generation_json_parser_accepts_fenced_object_and_rejects_non_object() -> None:
    assert _parse_json_object('```json\n{"decision":"answer"}\n```', "answer") == {
        "decision": "answer"
    }
    with pytest.raises(ValueError, match="did not return"):
        _parse_json_object("no structured result", "judge")


def test_generation_citation_contract_fails_closed_for_uncited_answers() -> None:
    assert (
        _accepted_decision(
            model_decision="answer",
            answer_payload={
                "decision": "answer",
                "claims": [{"claim_id": "c1", "text": "fact", "citation_ids": []}],
            },
            answer_citations=(),
        )
        == "abstain"
    )
    assert (
        _accepted_decision(
            model_decision="answer",
            answer_payload={
                "decision": "answer",
                "claims": [{"claim_id": "c1", "text": "fact", "citation_ids": ["kcite_1"]}],
            },
            answer_citations=("kcite_1",),
        )
        == "answer"
    )


def test_generation_rewrite_budget_is_bounded_and_requires_retry_decision() -> None:
    assert _rewrite_queries({"decision": "answer", "rewrite_queries": ["one", "two"]}, 2) == ()
    assert _rewrite_queries(
        {"decision": "retry", "rewrite_queries": ["one", "two", "three"]}, 2
    ) == ("one", "two")
