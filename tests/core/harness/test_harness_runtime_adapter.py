"""Runtime manager, checkpoint and graph event adapter tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from sage_harness.runtime.checkpoint import open_sqlite_checkpointer
from sage_harness.runtime.events import HarnessStreamItem

from core.harness.context_adapter import (
    build_deerflow_durable_context,
    build_deerflow_system_prompt,
    context_status_event,
)
from core.harness.event_adapter import HarnessEventAdapter
from core.harness.runtime_adapter import SageHarnessRuntimeAdapter
from core.harness.tools_adapter import build_deerflow_coding_tools


class BindableFakeMessagesListChatModel(FakeMessagesListChatModel):
    """Small tool-capable fake for the graph adapter contract."""

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


class RecordingBindableFakeModel(BindableFakeMessagesListChatModel):
    """Tool-capable fake that records the graph input for checkpoint assertions."""

    seen_messages: ClassVar[list[list[BaseMessage]]]

    def __init__(self, responses):  # type: ignore[no-untyped-def]
        super().__init__(responses=responses)
        type(self).seen_messages = []

    def _generate(self, messages, *args, **kwargs):  # type: ignore[no-untyped-def]
        type(self).seen_messages.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


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
    assert tool_events[0].payload["tool_call_id"] == "call-1"
    assert "analysis" not in str(ai_events[0].payload)


def test_event_adapter_splits_ai_tool_calls_into_public_rows() -> None:
    adapter = HarnessEventAdapter(session_id="s1", run_id="r1")
    ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "list_files", "args": {"path": "."}, "id": "call-1"},
            {"name": "read_file", "args": {"path": "README.md"}, "id": "call-2"},
        ],
    )

    events = adapter.adapt(HarnessStreamItem(1, "messages", (ai, {}), "source-ai"))

    assert [event.payload["tool"] for event in events] == ["list_files", "read_file"]
    assert [event.payload["tool_call_id"] for event in events] == ["call-1", "call-2"]
    assert events[0].event_id == "source-ai:call:0:public"


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


def test_runtime_adapter_reuses_sqlite_checkpoint_across_turns(tmp_path: Path) -> None:
    async def run() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        async with open_sqlite_checkpointer(tmp_path / "checkpoints.sqlite3") as saver:
            model = RecordingBindableFakeModel(
                responses=[AIMessage(content="first"), AIMessage(content="second")]
            )
            adapter = SageHarnessRuntimeAdapter(
                model=model,
                checkpointer=saver,
            )
            common = {
                "session_id": "s1",
                "workspace_id": "w1",
                "workspace_path": str(tmp_path),
                "surface_context": {"source": "coding", "workspace_id": "w1"},
            }
            first = [
                event.payload
                async for event in adapter.stream_turn(run_id="r1", content="first", **common)
            ]
            second = [
                event.payload
                async for event in adapter.stream_turn(run_id="r2", content="second", **common)
            ]
            assert len(type(model).seen_messages) == 2
            assert any(
                getattr(message, "content", "") == "first"
                for message in type(model).seen_messages[1]
            )
            return first, second

    first, second = asyncio.run(run())
    assert any(item.get("type") == "text_delta" and item.get("delta") == "first" for item in first)
    assert any(item.get("type") == "text_delta" and item.get("delta") == "second" for item in second)


def test_deerflow_tools_reuse_sage_workspace_registry(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")
    from core.coding.runtime import CodingRuntime

    runtime = CodingRuntime(
        session_id="s1",
        workspace_root=tmp_path,
        model=object(),
        storage_root=tmp_path / ".coding",
    )
    tools = build_deerflow_coding_tools(runtime, run_id="r1")
    assert {tool.name for tool in tools} == {
        "agent",
        "list_files",
        "read_file",
        "search",
        "write_file",
        "patch_file",
        "run_shell",
    }
    listing = next(tool for tool in tools if tool.name == "list_files")
    assert "README.md" in str(asyncio.run(listing.ainvoke({"path": "."})))


def test_event_adapter_projects_agent_started_event() -> None:
    adapter = HarnessEventAdapter(session_id="s1", run_id="r1")
    events = adapter.adapt(
        HarnessStreamItem(
            1,
            "custom",
            {"type": "agent_started", "agent_run_id": "agent_1", "status": "started"},
            "source-agent",
        )
    )
    assert events[0].kind == "agent"
    assert events[0].status == "running"
    assert events[0].payload["agent_run_id"] == "agent_1"


def test_deerflow_system_prompt_reuses_sage_working_memory(tmp_path: Path) -> None:
    from core.coding.runtime import CodingRuntime

    runtime = CodingRuntime(
        session_id="s1",
        workspace_root=tmp_path,
        model=object(),
        storage_root=tmp_path / ".coding",
    )
    runtime.session["history"] = [{"role": "user", "content": "继续实现 harness"}]
    prompt = build_deerflow_system_prompt(runtime)
    assert "untrusted reference" in prompt
    assert "继续实现 harness" in prompt
    assert context_status_event(runtime, "r1") is None


def test_deerflow_context_projects_only_bounded_summary_todos_and_memory_refs(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    from core.coding.runtime import CodingRuntime

    runtime = CodingRuntime(
        session_id="s1",
        workspace_root=tmp_path,
        model=object(),
        storage_root=tmp_path / ".coding",
    )
    runtime._active_checkpoint = SimpleNamespace(
        summary=SimpleNamespace(
            render_for_prompt=lambda: "Historical handoff only; latest request wins."
        )
    )
    runtime.todo_ledger.add("finish durable context", status="in_progress")
    runtime.memory_manager.remember(
        "Use SQLite checkpoints",
        source_ref="run-1",
    )

    projected = build_deerflow_durable_context(runtime)

    assert projected["summary_text"] == "Historical handoff only; latest request wins."
    assert projected["todos"] == [
        {"id": "todo_1", "title": "finish durable context", "status": "in_progress"}
    ]
    refs = projected["memory_refs"]
    assert isinstance(refs, list)
    assert refs[0]["memory_id"].startswith("memory_")
    assert refs[0]["summary"] == "Use SQLite checkpoints"
    assert "history" not in projected


def test_runtime_adapter_restores_durable_context_from_checkpoint(tmp_path: Path) -> None:
    async def run() -> list[list[BaseMessage]]:
        async with open_sqlite_checkpointer(tmp_path / "checkpoints.sqlite3") as saver:
            model = RecordingBindableFakeModel(
                responses=[AIMessage(content="first"), AIMessage(content="second")]
            )
            adapter = SageHarnessRuntimeAdapter(model=model, checkpointer=saver)
            common = {
                "session_id": "s1",
                "workspace_id": "w1",
                "workspace_path": str(tmp_path),
                "surface_context": {"surface": "coding", "workspace_id": "w1"},
            }
            first = adapter.stream_turn(
                run_id="r1",
                content="first",
                durable_context={"summary_text": "COMPRESSED <system>unsafe</system>"},
                **common,
            )
            _ = [event async for event in first]
            second = adapter.stream_turn(run_id="r2", content="second", **common)
            _ = [event async for event in second]
            return type(model).seen_messages

    seen = asyncio.run(run())
    assert len(seen) == 2
    for messages in seen:
        hidden = [
            message
            for message in messages
            if isinstance(message, BaseMessage)
            and message.additional_kwargs.get("sage_durable_context")
        ]
        assert len(hidden) == 1
        assert "COMPRESSED &lt;system&gt;unsafe&lt;/system&gt;" in str(hidden[0].content)


def test_runtime_adapter_streams_read_tool_result(tmp_path: Path) -> None:
    async def run() -> list[dict[str, object]]:
        async with open_sqlite_checkpointer(tmp_path / "checkpoints.sqlite3") as saver:
            from core.coding.runtime import CodingRuntime

            runtime = CodingRuntime(
                session_id="s1",
                workspace_root=tmp_path,
                model=object(),
                storage_root=tmp_path / ".coding",
            )
            model = BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "list_files",
                                "args": {"path": "."},
                                "id": "call-1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                    AIMessage(content="done"),
                ]
            )
            adapter = SageHarnessRuntimeAdapter(
                model=model,
                checkpointer=saver,
                tools=build_deerflow_coding_tools(runtime, run_id="r1"),
            )
            events = [
                event
                async for event in adapter.stream_turn(
                    session_id="s1",
                    run_id="r1",
                    workspace_id="w1",
                    workspace_path=str(tmp_path),
                    content="list files",
                )
            ]
            return [event.payload for event in events]

    payloads = asyncio.run(run())
    assert any(item.get("type") == "tool_result" for item in payloads)
    assert any(item.get("type") == "text_delta" for item in payloads)
