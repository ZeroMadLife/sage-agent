"""Runtime manager, checkpoint and graph event adapter tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from sage_harness.runtime.checkpoint import open_sqlite_checkpointer
from sage_harness.runtime.events import HarnessStreamItem

from core.harness.event_adapter import HarnessEventAdapter
from core.harness.runtime_adapter import SageHarnessRuntimeAdapter


def test_event_adapter_exposes_ai_delta_and_tool_result_without_private_state() -> None:
    adapter = HarnessEventAdapter(session_id="s1", run_id="r1")
    ai = AIMessage(content="公开回答", id="ai-1")
    tool = ToolMessage(content="README", tool_call_id="call-1", name="read_file", id="tool-1")

    ai_events = adapter.adapt(HarnessStreamItem(1, "messages", (ai, {}), "source-ai"))
    tool_events = adapter.adapt(HarnessStreamItem(2, "messages", (tool, {}), "source-tool"))

    assert ai_events[0].payload["type"] == "text_delta"
    assert ai_events[0].payload["delta"] == "公开回答"
    assert ai_events[0].event_id == "source-ai:public"
    assert tool_events[0].payload["type"] == "tool_result"
    assert "analysis" not in str(ai_events[0].payload)


def test_event_adapter_summarizes_values_instead_of_dumping_checkpoint() -> None:
    adapter = HarnessEventAdapter(session_id="s1", run_id="r1")
    events = adapter.adapt(
        HarnessStreamItem(
            1,
            "values",
            {"messages": [AIMessage(content="answer")], "surface_context": {"token": "x"}},
            "source-values",
        )
    )

    assert events[0].kind == "harness"
    assert events[0].payload["type"] == "checkpoint_update"
    assert "token" not in str(events[0].payload)


def test_runtime_adapter_streams_a_real_langgraph_message(tmp_path: Path) -> None:
    async def run() -> list[dict[str, object]]:
        async with open_sqlite_checkpointer(tmp_path / "checkpoints.sqlite3") as saver:
            adapter = SageHarnessRuntimeAdapter(
                model=FakeMessagesListChatModel(responses=[AIMessage(content="hello")]),
                checkpointer=saver,
            )
            events = [
                event
                async for event in adapter.stream_turn(
                    session_id="s1",
                    run_id="r1",
                    workspace_id="w1",
                    workspace_path="/tmp",
                    content="hi",
                )
            ]
            return [event.payload for event in events]

    payloads = asyncio.run(run())
    assert any(item.get("type") == "text_delta" for item in payloads)
