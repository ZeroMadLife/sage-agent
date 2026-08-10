"""不可变 Turn Context Plan 的恢复投影和作用域校验。"""

from __future__ import annotations

import hashlib

import pytest
from sage_harness import McpLifecycleSnapshot

from core.coding.skills import SkillLifecycleSnapshot
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
        tools={
            "skill_scope_active": True,
            "skill_allowlist": ["search_web"],
            "mcp_lifecycle": McpLifecycleSnapshot(
                config_revision="mcp-r1",
                scope_fingerprint="sha256:" + "1" * 64,
                catalog_hash="mcp-catalog-r1",
                tool_ids=("docs:lookup",),
            ).as_dict(),
            "skill_lifecycle": SkillLifecycleSnapshot(
                catalog_revision="skill-catalog-r1",
                activation_ref="skill://project/review",
                activation_revision="skill-r1",
                allowed_tools=("search_web",),
            ).as_dict(),
        },
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
    assert prepared.mcp_lifecycle is not None
    assert prepared.mcp_lifecycle.config_revision == "mcp-r1"
    assert prepared.skill_lifecycle is not None
    assert prepared.skill_lifecycle.activation_revision == "skill-r1"
    assert prepared.binding == {
        "version": 1,
        "run_id": "run-1",
        "plan_id": "tcp-resume",
        "plan_hash": _plan().plan_hash,
    }


def test_prepare_resume_fails_closed_when_skill_lifecycle_is_missing() -> None:
    payload = _plan().to_payload()
    tools = payload["tools"]
    assert isinstance(tools, dict)
    tools.pop("skill_lifecycle")
    original = _plan()
    legacy = TurnContextPlan.create(
        **original.identity_kwargs(),
        created_at=original.created_at,
        admission=payload["admission"],
        prompt=payload["prompt"],
        context_refs=payload["context_refs"],
        retrieval=payload["retrieval"],
        tools=tools,
        execution=payload["execution"],
        resume=payload["resume"],
    )

    with pytest.raises(TurnContextResumeError) as caught:
        prepare_turn_context_resume(
            legacy,
            session_id="session-1",
            run_id="run-1",
            owner_id="owner-1",
            workspace_id="workspace-1",
            surface="coding",
        )

    assert caught.value.code == "resume_skill_lifecycle_missing"


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
