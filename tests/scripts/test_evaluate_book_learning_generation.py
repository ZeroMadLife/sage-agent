from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.evaluate_book_learning_generation import (
    ModelInvocationError,
    _accepted_decision,
    _invoke_json,
    _model_receipt,
    _parse_json_object,
    _rewrite_queries,
    _runtime_metrics,
)


class _NeverRespondingLLM:
    async def ainvoke(self, _prompt: str) -> object:
        await __import__("asyncio").Event().wait()
        raise AssertionError("unreachable")


def test_generation_json_parser_accepts_fenced_object_and_rejects_non_object() -> None:
    assert _parse_json_object('```json\n{"decision":"answer"}\n```', "answer") == {
        "decision": "answer"
    }
    with pytest.raises(ValueError, match="did not return"):
        _parse_json_object("no structured result", "judge")


@pytest.mark.asyncio
async def test_generation_model_call_times_out_with_stage_receipt() -> None:
    with pytest.raises(ModelInvocationError) as captured:
        await _invoke_json(
            _NeverRespondingLLM(),
            "prompt",
            "planner_final",
            timeout_seconds=0.01,
        )

    assert captured.value.stage == "planner_final"
    assert captured.value.error_type == "timeout"


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
    assert (
        _accepted_decision(
            model_decision="answer",
            answer_payload={
                "decision": "answer",
                "claims": [{"claim_id": "c1", "text": "fact", "citation_ids": ["kcite_forged"]}],
            },
            answer_citations=("kcite_1",),
        )
        == "abstain"
    )


def test_generation_rewrite_budget_is_bounded_and_requires_retry_decision() -> None:
    assert _rewrite_queries({"decision": "answer", "rewrite_queries": ["one", "two"]}, 2) == ()
    assert _rewrite_queries(
        {"decision": "retry", "rewrite_queries": ["one", "two", "three"]}, 2
    ) == ("one", "two")


def test_generation_runtime_receipt_aggregates_tokens_latency_and_model_name() -> None:
    metrics = _runtime_metrics(
        [
            {
                "latency_ms": 100,
                "rewrite_queries": [],
                "accepted_decision": "answer",
                "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
            },
            {
                "latency_ms": 250,
                "rewrite_queries": ["rewrite"],
                "accepted_decision": "abstain",
                "usage": {"input_tokens": 20, "output_tokens": 6, "total_tokens": 26},
            },
        ]
    )

    assert metrics["p50_latency_ms"] == 100
    assert metrics["p95_latency_ms"] == 250
    assert metrics["recovery_activation_rate"] == 0.5
    assert metrics["provider_failure_count"] == 0
    assert metrics["provider_failure_rate"] == 0.0
    assert metrics["token_usage"] == {
        "input_tokens": 30,
        "output_tokens": 10,
        "total_tokens": 40,
    }
    assert metrics["cost_status"] == "not_computed_without_frozen_price_table"
    assert _model_receipt(
        SimpleNamespace(
            response_metadata={
                "model_name": "judge-revision-1",
                "system_fingerprint": "fp_1",
            }
        )
    ) == {"model_name": "judge-revision-1", "system_fingerprint": "fp_1"}


def test_generation_runtime_receipt_counts_provider_failures_separately() -> None:
    metrics = _runtime_metrics(
        [
            {
                "latency_ms": 60_000,
                "rewrite_queries": [],
                "accepted_decision": "abstain",
                "usage": {},
                "failure": {"stage": "planner", "error_type": "timeout"},
            }
        ]
    )

    assert metrics["provider_failure_count"] == 1
    assert metrics["provider_failure_rate"] == 1.0
