"""不可变 Turn Context Plan 的恢复投影和作用域校验。"""

from __future__ import annotations

import hashlib

import pytest

from core.harness.turn_context_plan import TurnContextPlan
from core.harness.turn_context_resume import TurnContextResumeError, prepare_turn_context_resume


def _plan() -> TurnContextPlan:
    owner_fingerprint = "sha256:" + hashlib.sha256(b"owner-1").hexdigest()
    return TurnContextPlan.create(
        plan_id="tcp-resume",
        session_id="session-1",
        run_id="run-1",
        owner_fingerprint=owner_fingerprint,
        workspace_id="workspace-1",
        surface="coding",
        created_at="2026-08-08T00:00:00+00:00",
        admission={},
        prompt={"static_policy": {"rendered_content": "policy"}},
        context_refs={},
        retrieval={
            "selected_sources": ["web"],
            "tool_scope": "retrieval_only",
        },
        tools={"skill_scope_active": True, "skill_allowlist": ["search_web"]},
        execution={},
        resume={
            "checkpoint_thread_id": "session-1",
            "checkpoint_namespace": "",
            "recovery_policy": "fail_closed",
        },
    )


def test_prepare_resume_uses_only_frozen_plan_routing_and_binding() -> None:
    prepared = prepare_turn_context_resume(
        _plan(),
        session_id="session-1",
        run_id="run-1",
        owner_id="owner-1",
        workspace_id="workspace-1",
        surface="coding",
    )

    assert prepared.retrieval_sources == frozenset({"web"})
    assert prepared.retrieval_tool_scope == "retrieval_only"
    assert prepared.active_skill_allowed_tools == frozenset({"search_web"})
    assert prepared.binding == {
        "version": 1,
        "run_id": "run-1",
        "plan_id": "tcp-resume",
        "plan_hash": _plan().plan_hash,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "run-2"),
        ("owner_id", "owner-2"),
        ("workspace_id", "workspace-2"),
        ("surface", "assistant"),
    ],
)
def test_prepare_resume_fails_closed_on_scope_drift(field: str, value: str) -> None:
    inputs = {
        "session_id": "session-1",
        "run_id": "run-1",
        "owner_id": "owner-1",
        "workspace_id": "workspace-1",
        "surface": "coding",
    }
    inputs[field] = value

    with pytest.raises(TurnContextResumeError) as caught:
        prepare_turn_context_resume(_plan(), **inputs)

    assert caught.value.code == "resume_plan_scope_mismatch"
