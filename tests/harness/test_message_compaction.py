"""Graph message compaction preserves tool protocol boundaries."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from sage_harness.runtime.message_compaction import (
    GraphMessageCompactionError,
    GraphMessageCompactionRequest,
    build_graph_message_compaction_plan,
)


def _request(*, keep: int) -> GraphMessageCompactionRequest:
    return GraphMessageCompactionRequest(
        compaction_id="compact-1",
        summary_text="bounded historical handoff",
        keep_recent_messages=keep,
    )


def test_compaction_moves_cutoff_before_ai_tool_pair() -> None:
    messages = [
        HumanMessage(content="old", id="h-old"),
        AIMessage(
            content="",
            id="ai-tool",
            tool_calls=[{"name": "read_file", "args": {"path": "README.md"}, "id": "call-1"}],
        ),
        ToolMessage(content="content", tool_call_id="call-1", id="tool-1"),
        AIMessage(content="done", id="ai-done"),
        HumanMessage(content="latest", id="h-latest"),
    ]

    plan = build_graph_message_compaction_plan(messages, _request(keep=3))

    assert plan.removed_message_count == 1
    assert plan.preserved_message_count == 4
    assert isinstance(plan.message_updates[0], RemoveMessage)
    assert plan.message_updates[0].id == REMOVE_ALL_MESSAGES
    assert [message.id for message in plan.message_updates[1:]] == [
        "ai-tool",
        "tool-1",
        "ai-done",
        "h-latest",
    ]


def test_compaction_preserves_unresolved_tool_call_before_recent_tail() -> None:
    messages = [
        HumanMessage(content="old", id="h-old"),
        AIMessage(
            content="",
            id="ai-pending",
            tool_calls=[{"name": "write_file", "args": {"path": "a.txt"}, "id": "call-pending"}],
        ),
        HumanMessage(content="later", id="h-later"),
        AIMessage(content="later answer", id="ai-later"),
        HumanMessage(content="latest", id="h-latest"),
    ]

    plan = build_graph_message_compaction_plan(messages, _request(keep=2))

    assert plan.removed_message_count == 1
    assert next(message.id for message in plan.message_updates[1:]) == "ai-pending"
    assert plan.state_update()["summary_text"] == "bounded historical handoff"


def test_compaction_rejects_orphaned_tool_message_at_cutoff() -> None:
    messages = [
        HumanMessage(content="old", id="h-old"),
        ToolMessage(content="orphan", tool_call_id="missing-call", id="tool-orphan"),
        HumanMessage(content="latest", id="h-latest"),
    ]

    with pytest.raises(
        GraphMessageCompactionError,
        match="without a matching AI tool call",
    ):
        build_graph_message_compaction_plan(messages, _request(keep=2))
