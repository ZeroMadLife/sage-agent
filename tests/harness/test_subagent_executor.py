"""Awaited child-agent tool and delegation ledger contract tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import MagicMock

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from sage_harness import (
    HarnessRunContext,
    SubagentLifecycleMiddleware,
    SubagentLimits,
    SubagentRequest,
    SubagentResult,
    SubagentToolConfig,
    build_task_tool,
    derive_child_run_id,
)
from sage_harness.agents import create_sage_agent
from sage_harness.middleware import MiddlewareSpec, build_default_registry


@dataclass
class FakeExecutor:
    result_text: str = "Found the requested evidence."
    delay: float = 0
    requests: list[SubagentRequest] = field(default_factory=list)
    cancelled: list[tuple[str, str]] = field(default_factory=list)

    async def execute(self, request: SubagentRequest) -> SubagentResult:
        self.requests.append(request)
        if self.delay:
            await asyncio.sleep(self.delay)
        return SubagentResult(
            child_run_id=request.child_run_id,
            status="succeeded",
            result=self.result_text,
            result_ref=f"subagent://{request.child_run_id}/result",
        )

    async def cancel(self, child_run_id: str, reason: str = "parent_cancelled") -> None:
        self.cancelled.append((child_run_id, reason))


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # type: ignore[no-untyped-def]
        _ = tools, tool_choice, kwargs
        return self


def _context(run_id: str = "run-parent") -> HarnessRunContext:
    return HarnessRunContext(
        thread_id="thread-parent",
        run_id=run_id,
        owner_id="owner-parent",
        workspace_id="workspace-parent",
        workspace_path="/workspace",
    )


def _task_call(call_id: str, description: str = "inspect code") -> dict[str, object]:
    return {
        "name": "task",
        "args": {
            "description": description,
            "prompt": "Inspect the repository without changing files.",
            "subagent_type": "Explore",
        },
        "id": call_id,
        "type": "tool_call",
    }


def test_subagent_limits_record_only_allowed_task_calls() -> None:
    middleware = SubagentLifecycleMiddleware(
        SubagentLimits(max_concurrent=2, max_total_per_run=2)
    )
    message = AIMessage(
        content="",
        tool_calls=[_task_call(f"call-{index}") for index in range(4)],
    )

    update = middleware.after_model(
        {"messages": [message]},
        MagicMock(context=_context()),
    )

    assert update is not None
    assert [item["id"] for item in update["delegations"]] == [
        derive_child_run_id("thread-parent", "run-parent", "call-0"),
        derive_child_run_id("thread-parent", "run-parent", "call-1"),
    ]
    replacement = update["messages"][0]
    assert isinstance(replacement, AIMessage)
    assert [call["id"] for call in replacement.tool_calls] == ["call-0", "call-1"]
    assert "SUBAGENT LIMIT REACHED" in str(replacement.content)


def test_subagent_lifecycle_closes_only_stale_running_entries() -> None:
    middleware = SubagentLifecycleMiddleware()
    update = middleware.before_agent(
        {
            "messages": [HumanMessage(content="continue")],
            "delegations": [
                {"id": "old", "run_id": "run-old", "status": "running"},
                {"id": "current", "run_id": "run-current", "status": "running"},
                {"id": "done", "run_id": "run-old", "status": "succeeded"},
            ],
        },
        MagicMock(context=_context("run-current")),
    )

    assert update == {
        "delegations": [
            {
                "id": "old",
                "run_id": "run-old",
                "status": "cancelled",
                "result_brief": "Parent run ended before the child returned.",
            }
        ]
    }


def test_real_graph_waits_for_child_and_persists_terminal_result() -> None:
    executor = FakeExecutor()
    model = ToolModel(
        responses=[
            AIMessage(content="", tool_calls=[_task_call("call-task")]),
            AIMessage(content="Parent used the child result."),
        ]
    )
    registry = build_default_registry().with_spec(
        MiddlewareSpec(
            "subagent_lifecycle",
            lambda config: SubagentLifecycleMiddleware(),
        ),
        before="durable_context",
    )
    graph = create_sage_agent(
        model,
        tools=[build_task_tool(executor)],
        registry=registry,
    )

    result = asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="Inspect the repository")]},
            context=_context(),
        )
    )

    assert len(executor.requests) == 1
    request = executor.requests[0]
    assert request.tool_scope == ("list_files", "read_file", "search")
    assert request.child_run_id.startswith("child_")
    assert result["delegations"][0]["status"] == "succeeded"
    assert result["delegations"][0]["result_ref"].startswith("subagent://")
    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert len(tool_messages) == 1
    assert "Found the requested evidence" in str(tool_messages[0].content)
    assert tool_messages[0].additional_kwargs["sage_subagent"]["status"] == "succeeded"


def test_task_tool_times_out_and_requests_child_cancellation() -> None:
    executor = FakeExecutor(delay=0.5)
    model = ToolModel(
        responses=[
            AIMessage(content="", tool_calls=[_task_call("call-timeout")]),
            AIMessage(content="Reported the timeout."),
        ]
    )
    graph = create_sage_agent(
        model,
        tools=[
            build_task_tool(
                executor,
                SubagentToolConfig(timeout_seconds=0.1),
            )
        ],
        registry=build_default_registry().with_spec(
            MiddlewareSpec(
                "subagent_lifecycle",
                lambda config: SubagentLifecycleMiddleware(),
            ),
            before="durable_context",
        ),
    )

    result = asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="Run a bounded child")]},
            context=_context(),
        )
    )

    assert executor.cancelled == [(executor.requests[0].child_run_id, "timeout")]
    assert result["delegations"][0]["status"] == "timed_out"
    tool_message = next(
        message for message in result["messages"] if isinstance(message, ToolMessage)
    )
    assert tool_message.status == "error"
    assert "timed out" in str(tool_message.content)


def test_task_tool_rejects_unconfigured_child_type_without_execution() -> None:
    executor = FakeExecutor()
    model = ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        **_task_call("call-worker"),
                        "args": {
                            "description": "edit code",
                            "prompt": "Change the repository.",
                            "subagent_type": "worker",
                        },
                    }
                ],
            ),
            AIMessage(content="Worker was rejected."),
        ]
    )
    graph = create_sage_agent(
        model,
        tools=[build_task_tool(executor)],
        registry=build_default_registry().with_spec(
            MiddlewareSpec(
                "subagent_lifecycle",
                lambda config: SubagentLifecycleMiddleware(),
            ),
            before="durable_context",
        ),
    )

    result = asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="Delegate a write task")]},
            context=_context(),
        )
    )

    assert executor.requests == []
    assert result["delegations"][0]["status"] == "failed"
    assert result["delegations"][0]["result_ref"] == ""
