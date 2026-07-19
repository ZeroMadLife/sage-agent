"""Reducer contracts for SageThreadState."""

from __future__ import annotations

import pytest
from langgraph.graph import END, START, StateGraph
from sage_harness.state import (
    SageThreadState,
    delegation_budget_usage,
    merge_approval_context,
    merge_artifacts,
    merge_delegations,
    merge_evidence_refs,
    merge_goal,
    merge_memory_refs,
    merge_promoted_tools,
    merge_sandbox,
    merge_skill_context,
    merge_thread_data,
    merge_todos,
)


def test_state_extends_langchain_agent_state() -> None:
    """Messages remain the LangChain reducer while Sage adds durable channels."""
    assert "messages" in SageThreadState.__annotations__
    assert "artifacts" in SageThreadState.__annotations__
    assert "approval_context" in SageThreadState.__annotations__


def test_langgraph_applies_sage_reducers_during_a_real_graph_run() -> None:
    """LangGraph must discover the annotated reducers, not merely accept the type."""

    def update_artifact(state: SageThreadState) -> dict[str, object]:
        assert state["artifacts"]
        return {"artifacts": [{"artifact_id": "a1", "path": "new"}]}

    graph = StateGraph(SageThreadState)
    graph.add_node("update", update_artifact)
    graph.add_edge(START, "update")
    graph.add_edge("update", END)

    result = graph.compile().invoke(
        {
            "messages": [],
            "artifacts": [{"artifact_id": "a1", "path": "old"}],
        }
    )

    assert result["artifacts"] == [{"artifact_id": "a1", "path": "new"}]


def test_none_preserves_and_explicit_empty_clears_collections() -> None:
    artifacts = [{"artifact_id": "a1", "path": "out.txt"}]
    todos = [{"id": "t1", "title": "Inspect", "status": "completed"}]

    assert merge_artifacts(artifacts, None) == artifacts
    assert merge_artifacts(artifacts, []) == []
    assert merge_todos(todos, None) == todos
    assert merge_todos(todos, []) == []


def test_artifacts_and_memory_refs_deduplicate_by_stable_id() -> None:
    assert merge_artifacts(
        [{"artifact_id": "a1", "path": "old"}],
        [{"artifact_id": "a1", "path": "new"}, {"artifact_id": "a2"}],
    ) == [{"artifact_id": "a1", "path": "new"}, {"artifact_id": "a2"}]
    assert merge_memory_refs(
        [{"memory_id": "m1", "summary": "old"}],
        [{"memory_id": "m1", "summary": "new"}, {"memory_id": "m2"}],
    ) == [{"memory_id": "m1", "summary": "new"}, {"memory_id": "m2"}]


def test_evidence_refs_merge_deterministically_across_child_completion_order() -> None:
    first = merge_evidence_refs(["wcite_b"], ["kcite_a", "wcite_b"])
    second = merge_evidence_refs(["kcite_a"], ["wcite_b", "kcite_a"])

    assert first == second == ["kcite_a", "wcite_b"]


def test_delegation_budget_usage_supports_reservations_actuals_and_legacy_state() -> None:
    assert delegation_budget_usage(
        {
            "status": "running",
            "reserved_tokens": 1_000,
            "reserved_model_calls": 4,
            "reserved_tool_calls": 2,
        }
    ) == (1_000, 4, 2)
    assert delegation_budget_usage(
        {
            "status": "succeeded",
            "token_usage": 600,
            "model_calls": 2,
            "tool_count": 1,
        }
    ) == (600, 2, 1)
    assert delegation_budget_usage(
        {"status": "succeeded", "token_budget": 24_000}
    ) == (24_000, 18, 16)
    assert delegation_budget_usage(
        {
            "status": "failed",
            "token_usage": 0,
            "model_calls": 0,
            "tool_count": 0,
        }
    ) == (0, 0, 0)


def test_promoted_tools_union_within_catalog_and_reset_on_schema_drift() -> None:
    first = {"catalog_hash": "catalog-a", "names": ["todo_list"]}

    assert merge_promoted_tools(first, {"catalog_hash": "catalog-a", "names": ["todo_add"]}) == {
        "catalog_hash": "catalog-a",
        "names": ["todo_list", "todo_add"],
    }
    assert merge_promoted_tools(first, {"catalog_hash": "catalog-b", "names": ["todo_add"]}) == {
        "catalog_hash": "catalog-b",
        "names": ["todo_add"],
    }
    with pytest.raises(ValueError, match="catalog_hash"):
        merge_promoted_tools(first, {"catalog_hash": "", "names": []})


def test_terminal_goal_and_delegation_cannot_be_downgraded() -> None:
    goal = {"goal_id": "g1", "status": "succeeded", "description": "done"}
    delegation = {"id": "d1", "status": "succeeded", "created_at": "now"}

    assert merge_goal(goal, {"goal_id": "g1", "status": "in_progress"}) == goal
    assert merge_delegations([delegation], [{"id": "d1", "status": "running"}]) == [delegation]

    with pytest.raises(ValueError, match="terminal goal statuses"):
        merge_goal(goal, {"goal_id": "g1", "status": "failed"})
    with pytest.raises(ValueError, match="terminal delegation statuses"):
        merge_delegations([delegation], [{"id": "d1", "status": "failed"}])


def test_skill_refs_are_bounded_and_normalized() -> None:
    refs = [
        {"path": f"skills/{index}", "description": "a  long\n description"} for index in range(10)
    ]

    merged = merge_skill_context(None, refs)

    assert len(merged) == 8
    assert merged[0]["path"] == "skills/2"
    assert merged[-1]["description"] == "a long description"


def test_conflicting_workspace_sandbox_and_approval_fail_closed() -> None:
    with pytest.raises(ValueError, match="workspace paths"):
        merge_thread_data({"workspace_path": "/one"}, {"workspace_path": "/two"})
    with pytest.raises(ValueError, match="owner ids"):
        merge_thread_data({"owner_id": "owner-a"}, {"owner_id": "owner-b"})
    with pytest.raises(ValueError, match="workspace ids"):
        merge_thread_data({"workspace_id": "workspace-a"}, {"workspace_id": "workspace-b"})
    with pytest.raises(ValueError, match="thread ids"):
        merge_thread_data({"thread_id": "thread-a"}, {"thread_id": "thread-b"})
    with pytest.raises(ValueError, match="sandbox ids"):
        merge_sandbox({"sandbox_id": "s1"}, {"sandbox_id": "s2"})
    with pytest.raises(ValueError, match="approval requests"):
        merge_approval_context(
            {"request_id": "r1", "status": "pending"}, {"request_id": "r2", "status": "pending"}
        )


def test_legacy_path_only_thread_state_can_claim_matching_durable_scope() -> None:
    assert merge_thread_data(
        {"workspace_path": "/workspace"},
        {
            "owner_id": "owner-a",
            "workspace_id": "workspace-a",
            "thread_id": "thread-a",
            "workspace_path": "/workspace",
        },
    ) == {
        "owner_id": "owner-a",
        "workspace_id": "workspace-a",
        "thread_id": "thread-a",
        "workspace_path": "/workspace",
    }


def test_approval_is_bound_to_exact_tool_call_and_arguments() -> None:
    pending = {
        "request_id": "r1",
        "tool_call_id": "call-1",
        "args_digest": "sha-a",
        "status": "pending",
    }

    with pytest.raises(ValueError, match="tool_call_id"):
        merge_approval_context(pending, {**pending, "tool_call_id": "call-2"})
    with pytest.raises(ValueError, match="args_digest"):
        merge_approval_context(pending, {**pending, "args_digest": "sha-b"})


def test_existing_checkpoint_channels_are_capped_on_read() -> None:
    artifacts = [{"artifact_id": f"a{index}"} for index in range(105)]
    memories = [{"memory_id": f"m{index}"} for index in range(40)]
    skills = [{"path": f"skills/{index}"} for index in range(10)]

    assert len(merge_artifacts(artifacts, None)) == 100
    assert len(merge_memory_refs(memories, None)) == 32
    assert len(merge_skill_context(skills, None)) == 8
