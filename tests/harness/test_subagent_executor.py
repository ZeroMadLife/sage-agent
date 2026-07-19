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
    SubagentProfile,
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
    token_usage: int = 0
    model_calls: int = 0
    tool_count: int = 0
    evidence_refs: tuple[str, ...] = ()
    requests: list[SubagentRequest] = field(default_factory=list)
    cancelled: list[tuple[str, str]] = field(default_factory=list)
    active: int = 0
    max_active: int = 0

    async def execute(self, request: SubagentRequest, progress=None) -> SubagentResult:  # type: ignore[no-untyped-def]
        self.requests.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if progress is not None:
            progress({"phase": "research", "status": "running"})
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            return SubagentResult(
                child_run_id=request.child_run_id,
                status="succeeded",
                result=self.result_text,
                result_ref=f"subagent://{request.child_run_id}/result",
                evidence_refs=self.evidence_refs,
                token_usage=self.token_usage,
                model_calls=self.model_calls,
                tool_count=self.tool_count,
            )
        finally:
            self.active -= 1

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
    middleware = SubagentLifecycleMiddleware(SubagentLimits(max_concurrent=2, max_total_per_run=2))
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


def test_subagent_limits_reserve_one_shared_parent_token_budget() -> None:
    config = SubagentToolConfig(
        allowed_types=frozenset({"explore", "research"}),
        profiles=(
            SubagentProfile(
                name="research",
                tool_scope=("knowledge_search", "search_web"),
                token_budget=24_000,
                timeout_seconds=180,
                max_steps=16,
            ),
        ),
    )
    middleware = SubagentLifecycleMiddleware(tool_config=config)
    calls = [_task_call(f"call-{index}") for index in range(3)]
    for call in calls:
        call["args"] = {**call["args"], "subagent_type": "research"}  # type: ignore[index]

    update = middleware.after_model(
        {
            "messages": [AIMessage(content="", tool_calls=calls)],
            "run_token_usage": 70_000,
            "run_token_limit": 100_000,
            "run_model_calls": 1,
            "run_model_call_limit": 24,
            "run_tool_calls": 3,
            "run_tool_call_limit": 64,
        },
        MagicMock(context=_context()),
    )

    assert update is not None
    assert [entry["reserved_tokens"] for entry in update["delegations"]] == [
        10_000,
        10_000,
        10_000,
    ]
    assert [entry["reserved_model_calls"] for entry in update["delegations"]] == [
        7,
        7,
        8,
    ]
    assert [entry["reserved_tool_calls"] for entry in update["delegations"]] == [
        5,
        5,
        6,
    ]


def test_subagent_limits_keep_the_first_call_when_only_one_minimum_slot_remains() -> None:
    middleware = SubagentLifecycleMiddleware()
    message = AIMessage(
        content="",
        tool_calls=[_task_call(f"call-{index}") for index in range(3)],
    )

    update = middleware.after_model(
        {
            "messages": [message],
            "run_token_usage": 99_500,
            "run_token_limit": 100_000,
            "run_model_calls": 1,
            "run_model_call_limit": 24,
            "run_tool_calls": 3,
            "run_tool_call_limit": 64,
        },
        MagicMock(context=_context()),
    )

    assert update is not None
    assert [entry["description"] for entry in update["delegations"]] == ["inspect code"]
    assert update["delegations"][0]["reserved_tokens"] == 500
    assert [call["id"] for call in update["messages"][0].tool_calls] == ["call-0"]


def test_subagent_limits_reuse_derived_child_identity_on_resume() -> None:
    middleware = SubagentLifecycleMiddleware()
    child_run_id = derive_child_run_id("thread-parent", "run-parent", "call-replay")

    update = middleware.after_model(
        {
            "messages": [AIMessage(content="", tool_calls=[_task_call("call-replay")])],
            "delegations": [
                {
                    "id": child_run_id,
                    "run_id": "run-parent",
                    "status": "running",
                    "reserved_tokens": 24_000,
                    "reserved_model_calls": 14,
                    "reserved_tool_calls": 12,
                }
            ],
            "run_token_limit": 100_000,
        },
        MagicMock(context=_context()),
    )

    assert update is None


def test_subagent_limits_upgrade_legacy_running_reservation_on_resume() -> None:
    middleware = SubagentLifecycleMiddleware()
    child_run_id = derive_child_run_id("thread-parent", "run-parent", "call-legacy")

    update = middleware.after_model(
        {
            "messages": [AIMessage(content="", tool_calls=[_task_call("call-legacy")])],
            "delegations": [
                {
                    "id": child_run_id,
                    "run_id": "run-parent",
                    "status": "running",
                }
            ],
            "run_token_limit": 100_000,
            "run_model_call_limit": 24,
            "run_tool_call_limit": 64,
        },
        MagicMock(context=_context()),
    )

    assert update is not None
    assert update["delegations"][0]["id"] == child_run_id
    assert update["delegations"][0]["reserved_tokens"] == 24_000
    assert update["delegations"][0]["reserved_model_calls"] == 14
    assert update["delegations"][0]["reserved_tool_calls"] == 12


def test_subagent_limits_drop_duplicate_child_identity_within_one_batch() -> None:
    middleware = SubagentLifecycleMiddleware()
    message = AIMessage(
        content="",
        tool_calls=[
            _task_call("call-duplicate", "first"),
            _task_call("call-duplicate", "second"),
        ],
    )

    update = middleware.after_model(
        {"messages": [message]},
        MagicMock(context=_context()),
    )

    assert update is not None
    assert len(update["delegations"]) == 1
    assert len(update["messages"][0].tool_calls) == 1
    assert update["messages"][0].tool_calls[0]["args"]["description"] == "first"


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
    executor = FakeExecutor(
        token_usage=1_200,
        model_calls=2,
        tool_count=2,
        evidence_refs=("kcite_a", "wcite_b"),
    )
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
    assert request.subagent_type == "explore"
    assert request.child_run_id.startswith("child_")
    assert result["delegations"][0]["status"] == "succeeded"
    assert result["delegations"][0]["token_usage"] == 1_200
    assert result["delegations"][0]["model_calls"] == 2
    assert result["delegations"][0]["tool_count"] == 2
    assert result["evidence_refs"] == ["kcite_a", "wcite_b"]
    assert result["delegations"][0]["result_ref"].startswith("subagent://")
    tool_messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert len(tool_messages) == 1
    assert "Found the requested evidence" in str(tool_messages[0].content)
    assert tool_messages[0].additional_kwargs["sage_subagent"]["status"] == "succeeded"


def test_task_tool_accepts_lowercase_explore_profile_from_model() -> None:
    executor = FakeExecutor()
    call = _task_call("call-lowercase")
    call["args"] = {**call["args"], "subagent_type": "explore"}  # type: ignore[index]
    model = ToolModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="Child evidence was used."),
        ]
    )
    graph = create_sage_agent(
        model,
        tools=[build_task_tool(executor)],
        registry=build_default_registry().with_spec(
            MiddlewareSpec(
                "subagent_lifecycle",
                lambda _: SubagentLifecycleMiddleware(),
            ),
            before="durable_context",
        ),
    )

    result = asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="Inspect the repository")]},
            context=_context(),
        )
    )

    assert executor.requests[0].subagent_type == "explore"
    assert result["delegations"][0]["status"] == "succeeded"


def test_real_graph_runs_three_bounded_children_concurrently() -> None:
    executor = FakeExecutor(delay=0.05)
    model = ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_task_call(f"call-parallel-{index}") for index in range(3)],
            ),
            AIMessage(content="Parent synthesized three child results."),
        ]
    )
    graph = create_sage_agent(
        model,
        tools=[build_task_tool(executor)],
        registry=build_default_registry().with_spec(
            MiddlewareSpec(
                "subagent_lifecycle",
                lambda _: SubagentLifecycleMiddleware(),
            ),
            before="durable_context",
        ),
    )

    result = asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="Inspect three bounded questions")]},
            context=_context(),
        )
    )

    assert len(executor.requests) == 3
    assert executor.max_active == 3
    assert sum(request.max_steps + 2 for request in executor.requests) + 2 <= 24
    assert sum(request.max_steps for request in executor.requests) + 3 <= 64
    assert [entry["status"] for entry in result["delegations"]] == [
        "succeeded",
        "succeeded",
        "succeeded",
    ]


def test_task_tool_applies_server_owned_research_profile() -> None:
    executor = FakeExecutor()
    call = _task_call("call-research")
    call["args"] = {**call["args"], "subagent_type": "research"}  # type: ignore[index]
    config = SubagentToolConfig(
        allowed_types=frozenset({"explore", "research"}),
        profiles=(
            SubagentProfile(
                name="research",
                tool_scope=(
                    "list_files",
                    "read_file",
                    "search",
                    "knowledge_search",
                    "search_web",
                    "fetch_web",
                ),
                token_budget=32_000,
                timeout_seconds=180,
                max_steps=16,
            ),
        ),
    )
    model = ToolModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="Research evidence was used."),
        ]
    )
    graph = create_sage_agent(
        model,
        tools=[build_task_tool(executor, config)],
        registry=build_default_registry().with_spec(
            MiddlewareSpec(
                "subagent_lifecycle",
                lambda _: SubagentLifecycleMiddleware(tool_config=config),
            ),
            before="durable_context",
        ),
    )

    result = asyncio.run(
        graph.ainvoke(
            {"messages": [HumanMessage(content="Research one bounded question")]},
            context=_context(),
        )
    )

    request = executor.requests[0]
    assert request.subagent_type == "research"
    assert request.tool_scope[-3:] == ("knowledge_search", "search_web", "fetch_web")
    assert request.token_budget == 32_000
    assert request.timeout_seconds == 180
    assert request.max_steps == 16
    assert result["delegations"][0]["status"] == "succeeded"


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
    assert result["delegations"][0]["token_usage"] == executor.requests[0].token_budget
    assert result["delegations"][0]["model_calls"] == executor.requests[0].max_steps + 2
    assert result["delegations"][0]["tool_count"] == executor.requests[0].max_steps
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
