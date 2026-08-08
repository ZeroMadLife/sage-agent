"""Turn Context Plan contract tests."""

from __future__ import annotations

import json

import pytest

from core.harness.turn_context_plan import (
    TurnContextPlan,
    TurnContextPlanValidationError,
    build_turn_context_plan_receipt,
)


def _plan(
    *, plan_id: str = "tcp-1", created_at: str = "2026-08-07T00:00:00+00:00"
) -> TurnContextPlan:
    return TurnContextPlan.create(
        plan_id=plan_id,
        session_id="session-1",
        run_id="run-1",
        owner_fingerprint="owner-1",
        workspace_id="workspace-1",
        surface="coding",
        created_at=created_at,
        admission={"input_origin": "user", "input_fingerprint": "input-sha"},
        prompt={
            "static_policy": {
                "template_id": "coding-harness",
                "revision": "v1",
                "rendered_content": "server-owned policy",
            },
            "dynamic_authority": {"retrieval_tool_scope": "default"},
        },
        context_refs={
            "transcript_range": [1, 4],
            "memory_refs": [{"memory_id": "memory-1", "revision": "2", "digest": "m-sha"}],
        },
        retrieval={"decision": "allow", "selected_sources": ["semantic_memory"]},
        tools={"catalog_hash": "catalog-sha", "resident_ids": ["read_file"], "deferred_ids": []},
        execution={"runtime_mode": "default", "sandbox_fingerprint": "sandbox-sha"},
        resume={"checkpoint_thread_id": "session-1", "checkpoint_namespace": ""},
    )


def test_plan_hash_is_canonical_and_excludes_runtime_identity_fields() -> None:
    first = _plan(plan_id="tcp-1", created_at="2026-08-07T00:00:00+00:00")
    second = _plan(plan_id="tcp-2", created_at="2026-08-07T01:00:00+00:00")

    assert first.plan_hash == second.plan_hash
    assert first.to_payload()["prompt"]["static_policy"]["content_hash"] == first.static_policy_hash


def test_checkpoint_binding_contains_no_plan_payload() -> None:
    plan = _plan()

    assert plan.checkpoint_binding() == {
        "version": 1,
        "run_id": "run-1",
        "plan_id": "tcp-1",
        "plan_hash": plan.plan_hash,
    }


def test_plan_rejects_secret_bearing_fields() -> None:
    with pytest.raises(TurnContextPlanValidationError, match="secret"):
        TurnContextPlan.create(
            **{
                **_plan().identity_kwargs(),
                "created_at": "2026-08-07T00:00:00+00:00",
                "admission": {},
                "prompt": {"static_policy": {"api_key": "must-not-store"}},
                "context_refs": {},
                "retrieval": {},
                "tools": {},
                "execution": {},
                "resume": {},
            }
        )


def test_plan_allows_token_budget_metadata_but_rejects_credential_tokens() -> None:
    plan = TurnContextPlan.create(
        **{
            **_plan().identity_kwargs(),
            "created_at": "2026-08-07T00:00:00+00:00",
            "admission": {},
            "prompt": {"static_policy": {"template_id": "coding-harness"}},
            "context_refs": {},
            "retrieval": {},
            "tools": {},
            "execution": {},
            "resume": {},
            "budget": {
                "token_budget": 8_192,
                "estimated_input_tokens": 1_024,
                "token_budget_by_source": {"transcript": 4_096},
            },
        }
    )

    assert plan.to_payload()["budget"]["token_budget"] == 8_192

    for secret_key in (
        "access_token",
        "github_token",
        "client_secret",
        "apiKey",
        "ａｐｉ＿ｋｅｙ",
    ):
        with pytest.raises(TurnContextPlanValidationError, match="secret"):
            TurnContextPlan.create(
                **{
                    **_plan().identity_kwargs(),
                    "created_at": "2026-08-07T00:00:00+00:00",
                    "admission": {},
                    "prompt": {"static_policy": {secret_key: "must-not-store"}},
                    "context_refs": {},
                    "retrieval": {},
                    "tools": {},
                    "execution": {},
                    "resume": {},
                }
            )


def test_plan_rejects_non_string_json_keys_instead_of_coercing_them() -> None:
    with pytest.raises(TurnContextPlanValidationError, match="key"):
        TurnContextPlan.create(
            **{
                **_plan().identity_kwargs(),
                "created_at": "2026-08-07T00:00:00+00:00",
                "admission": {1: "ambiguous-key"},
                "prompt": {},
                "context_refs": {},
                "retrieval": {},
                "tools": {},
                "execution": {},
                "resume": {},
            }
        )


def test_plan_receipt_excludes_untrusted_content_and_static_policy() -> None:
    plan = _plan()
    receipt = build_turn_context_plan_receipt(plan)
    rendered = json.dumps(receipt, sort_keys=True)

    assert receipt["type"] == "turn_context_plan_prepared"
    assert receipt["plan_hash"] == plan.plan_hash
    assert "server-owned policy" not in rendered
    assert "memory-1" not in rendered
    assert "rendered_content" not in rendered
