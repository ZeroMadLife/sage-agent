"""Turn-boundary context integration for the DeerFlow runtime profile."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from sage_harness import (
    McpConfigSnapshot,
    McpManager,
    McpScope,
    McpServerConfig,
    McpToolDescriptor,
)

import api.coding as coding_api
from core.coding.context import (
    CompactionCheckpoint,
    CompactionResult,
    CompactionSummary,
    ContextPolicy,
    PreparedContext,
)
from core.coding.context.budget import ContextUsage
from core.coding.engine.events import (
    ContextCompactionCompletedEvent,
    ContextCompactionStartedEvent,
    ContextUsageUpdatedEvent,
)
from core.coding.persistence.session_event_journal import SessionEventJournal
from core.coding.persistence.tool_result_store import ToolResultStore
from core.coding.persistence.turn_plan_store import TurnPlanStore
from core.coding.run_coordinator import RunEvent
from core.coding.runtime import CodingRuntime
from core.coding.skills import SkillRegistry
from core.harness.book_learning_coordinator import BookLearningCoordinatorOutcome
from core.harness.turn_context_comparator import TurnContextComparison
from core.harness.turn_context_plan import TurnContextPlan


def _usage(level: str = "normal") -> ContextUsage:
    used = 90_000 if level == "emergency" else 100
    return ContextUsage(
        used_tokens=used,
        effective_limit_tokens=100_000,
        usage_ratio=used / 100_000,
        level=level,  # type: ignore[arg-type]
        estimated=False,
    )


def _usage_event(run_id: str, level: str = "normal") -> ContextUsageUpdatedEvent:
    usage = _usage(level)
    return ContextUsageUpdatedEvent(
        session_id="session-context",
        run_id=run_id,
        used_tokens=usage.used_tokens,
        model_limit_tokens=110_000,
        output_reserve_tokens=10_000,
        effective_limit_tokens=usage.effective_limit_tokens,
        usage_ratio=usage.usage_ratio,
        level=usage.level,
        estimated=False,
        compactable=True,
    )


class AppliedContextController:
    """Return one deterministic compaction result at the graph turn boundary."""

    lifecycle_sink: Any = None

    async def on_turn_start(
        self,
        history: list[dict[str, Any]],
        user_message: str,
        run_id: str,
        **kwargs: Any,
    ) -> PreparedContext:
        del user_message, kwargs
        summary = CompactionSummary(
            goal="continue the migrated harness",
            source_transcript_range=(1, max(1, len(history))),
        )
        checkpoint = CompactionCheckpoint(
            compaction_id="compact-v2",
            transcript_start=1,
            transcript_end=max(1, len(history)),
            summary=summary,
            summary_hash="summary-hash",
        )
        projected = [
            {
                "role": "system",
                "kind": "compact_summary",
                "content": summary.render_for_prompt(),
            }
        ]
        result = CompactionResult(
            applied=True,
            projected_history=projected,
            checkpoint=checkpoint,
            before_tokens=1_000,
            after_tokens=100,
            archived_items=len(history),
            compaction_id="compact-v2",
            trigger="auto",
        )
        return PreparedContext.create(
            projected_history=projected,
            usage=_usage(),
            allow_model_request=True,
            compaction_result=result,
            events=(
                ContextCompactionStartedEvent(
                    session_id="session-context",
                    run_id=run_id,
                    compaction_id="compact-v2",
                    trigger="auto",
                    before_tokens=1_000,
                ),
                ContextCompactionCompletedEvent(
                    session_id="session-context",
                    run_id=run_id,
                    compaction_id="compact-v2",
                    before_tokens=1_000,
                    after_tokens=100,
                    archived_items=len(history),
                ),
                _usage_event(run_id),
            ),
        )

    def before_model_request(self, history: list[dict[str, Any]], **kwargs: Any) -> PreparedContext:
        del kwargs
        return PreparedContext.create(
            projected_history=history,
            usage=_usage(),
            allow_model_request=True,
        )


class EmergencyContextController:
    """Reject the graph model request after publishing emergency pressure."""

    lifecycle_sink: Any = None

    async def on_turn_start(
        self,
        history: list[dict[str, Any]],
        user_message: str,
        run_id: str,
        **kwargs: Any,
    ) -> PreparedContext:
        del user_message, kwargs
        return PreparedContext.create(
            projected_history=history,
            usage=_usage("emergency"),
            allow_model_request=False,
            events=(_usage_event(run_id, "emergency"),),
        )

    def before_model_request(self, history: list[dict[str, Any]], **kwargs: Any) -> PreparedContext:
        del kwargs
        return PreparedContext.create(
            projected_history=history,
            usage=_usage("emergency"),
            allow_model_request=False,
        )


def _runtime(
    tmp_path: Path,
    *,
    controller: AppliedContextController | EmergencyContextController | None = None,
) -> CodingRuntime:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return CodingRuntime(
        session_id="session-context",
        workspace_root=workspace,
        model=object(),
        storage_root=tmp_path / ".coding",
        session_state={
            "id": "session-context",
            "workspace_root": str(workspace),
            "history": [{"role": "user", "content": "old request"}],
            "runtime_profile": "deerflow_v2",
        },
        context_policy=ContextPolicy(
            context_window_tokens=110_000,
            output_reserve_tokens=10_000,
        ),
        context_controller=controller,  # type: ignore[arg-type]
        runtime_profile="deerflow_v2",
    )


class RecordingAdapter:
    runtime: ClassVar[CodingRuntime]
    durable_contexts: ClassVar[list[dict[str, object]]]
    graph_compactions: ClassVar[list[dict[str, object]]]
    init_kwargs: ClassVar[dict[str, object]]
    stream_kwargs: ClassVar[list[dict[str, object]]]

    def __init__(self, **kwargs: Any) -> None:
        type(self).init_kwargs = dict(kwargs)
        type(self).durable_contexts = []
        type(self).graph_compactions = []
        type(self).stream_kwargs = []

    async def stream_turn(self, **kwargs: Any):  # type: ignore[no-untyped-def]
        assert type(self).runtime.active_run_id == kwargs["run_id"]
        type(self).stream_kwargs.append(dict(kwargs))
        type(self).durable_contexts.append(dict(kwargs["durable_context"]))
        if kwargs.get("graph_compaction") is not None:
            type(self).graph_compactions.append(dict(kwargs["graph_compaction"]))
        yield RunEvent(
            kind="assistant",
            status="running",
            payload={"type": "text_delta", "delta": "graph answer"},
        )


class AvailableWebSearchPort:
    available = True

    async def search(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("resume routing test must not execute web search")


class AvailableKnowledgePort:
    available = True
    workspace_id = "knowledge-workspace"

    async def search(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("routing test must not execute Knowledge search")


class StaticPlanCheckpoint:
    """Expose one already-scoped Graph checkpoint for enforce resume tests."""

    def __init__(self, *, runtime: CodingRuntime, binding: dict[str, object]) -> None:
        self._checkpoint = SimpleNamespace(
            checkpoint={
                "channel_values": {
                    "thread_data": {
                        "owner_id": runtime.owner_user_id or "local",
                        "workspace_id": coding_api.workspace_id_from_path(runtime.workspace.root),
                        "thread_id": runtime.session_id,
                        "workspace_path": str(runtime.workspace.root),
                    },
                    "turn_context_plan": binding,
                }
            }
        )

    async def aget_tuple(self, config: object) -> object:
        del config
        return self._checkpoint


class CountingMcpTransport:
    """只记录 discovery 次数，验证 lifecycle preflight 不触发新连接。"""

    def __init__(self) -> None:
        self.discoveries = 0
        self.invalidated: list[str] = []

    async def discover(
        self,
        server: McpServerConfig,
        scope: McpScope,
    ) -> Sequence[McpToolDescriptor]:
        del scope
        self.discoveries += 1
        return (
            McpToolDescriptor.from_schema(
                tool_id=f"{server.name}:lookup",
                server_name=server.name,
                name=f"{server.name}_lookup",
                original_name="lookup",
                description="Lookup docs",
                schema={"type": "object", "properties": {}},
            ),
        )

    async def invoke(
        self,
        tool: McpToolDescriptor,
        arguments: Mapping[str, object],
        scope: McpScope,
    ) -> object:
        del tool, arguments, scope
        raise AssertionError("lifecycle test must not invoke MCP tools")

    async def close_scope(self, scope: McpScope) -> None:
        del scope

    async def invalidate_revision(self, revision: str) -> None:
        self.invalidated.append(revision)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_v2_compacts_before_graph_and_injects_new_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, controller=AppliedContextController())
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)

    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="new request",
            run_id="run-context",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
        )
    ]

    event_types = [str(event.payload.get("type", "")) for event in events]
    assert event_types.count("turn_context_plan_prepared") == 1
    assert event_types.count("turn_context_plan_compared") == 1
    comparison_event = next(
        event for event in events if event.payload.get("type") == "turn_context_plan_compared"
    )
    assert comparison_event.payload["matched"] is True
    assert event_types.count("context_compaction_started") == 1
    assert event_types.count("context_compaction_completed") == 1
    assert event_types.count("context_usage_updated") == 1
    assert "continue the migrated harness" in str(
        RecordingAdapter.durable_contexts[0]["summary_text"]
    )
    assert RecordingAdapter.graph_compactions == [
        {
            "compaction_id": "compact-v2",
            "summary_text": RecordingAdapter.durable_contexts[0]["summary_text"],
        }
    ]
    assert isinstance(RecordingAdapter.init_kwargs["artifact_store"], ToolResultStore)
    assert RecordingAdapter.stream_kwargs[0]["owner_id"] == "local"
    assert runtime.session["context_state"]["checkpoint_id"] == "compact-v2"
    assert runtime.active_run_id is None
    assert runtime.context_snapshot()["context_operation_active"] is False
    plan = TurnPlanStore(runtime.storage_root, runtime.session_id).load_for_run("run-context")
    assert plan is not None
    assert plan.to_payload()["context_refs"]["user_message_ref"]["sequence"] == 2
    assert [item["role"] for item in runtime.session["history"][-2:]] == [
        "user",
        "assistant",
    ]


@pytest.mark.asyncio
async def test_v2_emits_retrieval_gate_and_loads_only_selected_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    runtime.memory_manager.remember(
        "用户偏好先给出简短结论，再展开证据。",
        topic="project-conventions",
        source_ref="approved-memory",
    )
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)

    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="你还记得我之前告诉过你的偏好吗？",
            run_id="run-memory-gate",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
        )
    ]

    gate_index = next(
        index
        for index, event in enumerate(events)
        if event.payload.get("type") == "retrieval_gate_decided"
    )
    catalog_index = next(
        index
        for index, event in enumerate(events)
        if event.payload.get("type") == "capability_catalog_updated"
    )
    gate = events[gate_index].payload
    assert gate["decision"] == "semantic_memory"
    assert gate["selected_sources"] == ["semantic_memory"]
    assert gate["query_fingerprint"]
    assert "偏好" not in str(gate)
    assert gate_index < catalog_index

    durable = RecordingAdapter.durable_contexts[0]
    references = durable["memory_refs"]
    assert isinstance(references, list)
    assert references[0]["summary"] == "用户偏好先给出简短结论，再展开证据。"
    assert durable["retrieval_gate"]["decision"] == "semantic_memory"


@pytest.mark.asyncio
async def test_v2_gate_hard_routes_web_tool_visibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)

    skip_root = tmp_path / "skip"
    skip_root.mkdir()
    skip_runtime = _runtime(skip_root)
    RecordingAdapter.runtime = skip_runtime
    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            skip_runtime,
            content="1 + 1 等于多少？",
            run_id="run-web-skip",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            web_search_port=AvailableWebSearchPort(),  # type: ignore[arg-type]
        )
    ]
    skip_deferred = RecordingAdapter.init_kwargs["deferred_setup"]
    assert "search_web" not in skip_deferred.deferred_names
    skip_subagents = RecordingAdapter.init_kwargs["subagent_tool_config"]
    assert skip_subagents.resolve("research") is None

    web_root = tmp_path / "web"
    web_root.mkdir()
    web_runtime = _runtime(web_root)
    RecordingAdapter.runtime = web_runtime
    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            web_runtime,
            content="请联网搜索最新的 LangGraph checkpoint 官方资料。",
            run_id="run-web-selected",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            web_search_port=AvailableWebSearchPort(),  # type: ignore[arg-type]
        )
    ]
    selected_deferred = RecordingAdapter.init_kwargs["deferred_setup"]
    assert "search_web" in selected_deferred.deferred_names
    selected_subagents = RecordingAdapter.init_kwargs["subagent_tool_config"]
    assert selected_subagents.resolve("research").tool_scope == (
        "list_files",
        "read_file",
        "search",
        "search_web",
    )


@pytest.mark.asyncio
async def test_v2_gate_enforces_explicit_source_only_tool_scopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)
    monkeypatch.setattr(
        coding_api,
        "CodingKnowledgePort",
        lambda runtime: AvailableKnowledgePort(),
    )

    cases = (
        (
            "上次 shell 审批恢复那轮做了什么？只根据历史运行记录概括，不要联网。",
            None,
            set(),
        ),
        (
            "只检索 Sage 知识库中的 checkpoint 资料，不联网；若没有证据直接说明。",
            None,
            {"knowledge_search"},
        ),
        (
            "只搜索 LangGraph checkpoint 的官方资料，返回引用。",
            AvailableWebSearchPort(),
            {"search_web"},
        ),
    )
    for index, (content, web_port, expected_tools) in enumerate(cases):
        root = tmp_path / f"strict-{index}"
        root.mkdir()
        runtime = _runtime(root)
        RecordingAdapter.runtime = runtime
        _ = [
            event
            async for event in coding_api._deerflow_timeline_events(
                runtime,
                content=content,
                run_id=f"run-strict-{index}",
                surface_context={"surface": "coding"},
                thread_goal=None,
                checkpointer=object(),
                mcp_catalog=None,
                web_search_port=web_port,  # type: ignore[arg-type]
            )
        ]
        tools = RecordingAdapter.init_kwargs["tools"]
        deferred = RecordingAdapter.init_kwargs["deferred_setup"]
        harness_config = RecordingAdapter.init_kwargs["config"]
        assert {tool.name for tool in tools} == expected_tools
        assert deferred.deferred_names == frozenset()
        assert harness_config.max_model_calls == 6
        assert harness_config.max_tool_calls == 4
        assert harness_config.max_run_tokens == 64_000
        assert harness_config.max_run_seconds == 120.0
        system_prompt = str(RecordingAdapter.init_kwargs["system_prompt"])
        assert "source-locked" in system_prompt
        assert "at most four total retrieval calls" in system_prompt
        assert RecordingAdapter.init_kwargs["finalize_after_tool_calls"] == 4


@pytest.mark.asyncio
async def test_v2_gate_builds_knowledge_only_research_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)
    monkeypatch.setattr(
        coding_api,
        "CodingKnowledgePort",
        lambda runtime: AvailableKnowledgePort(),
    )

    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="请检索知识库里的 checkpoint 设计。",
            run_id="run-knowledge-selected",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
        )
    ]

    config = RecordingAdapter.init_kwargs["subagent_tool_config"]
    assert config.resolve("research").tool_scope == (
        "list_files",
        "read_file",
        "search",
        "knowledge_search",
    )


@pytest.mark.asyncio
async def test_v2_subagent_approval_continues_without_graph_checkpoint_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)

    class SubagentApprovalAdapter:
        calls = 0

        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        async def stream_turn(self, **kwargs: Any):  # type: ignore[no-untyped-def]
            del kwargs
            type(self).calls += 1
            yield RunEvent(
                kind="approval",
                status="blocked",
                payload={
                    "type": "approval_required",
                    "approval_id": "appr_child",
                    "tool": "write_file",
                    "approval_scope": "subagent",
                    "resume_required": False,
                },
            )
            yield RunEvent(
                kind="assistant",
                status="running",
                payload={"type": "text_delta", "delta": "continued in place"},
            )

    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", SubagentApprovalAdapter)

    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="practice",
            run_id="run-practice-approval",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
        )
    ]

    assert SubagentApprovalAdapter.calls == 1
    assert any(event.payload.get("type") == "approval_required" for event in events)
    assert events[-2].payload["content"] == "continued in place"


@pytest.mark.asyncio
async def test_v2_external_resume_preserves_checkpoint_retrieval_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)

    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="resume original request",
            run_id="run-resume-gate",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            resume_value={"interrupt-1": {"approval_id": "approval-1", "choice": "once"}},
            resume_attempt=1,
        )
    ]

    assert not any(event.payload.get("type") == "retrieval_gate_decided" for event in events)
    assert not any(event.payload.get("type") == "turn_context_plan_prepared" for event in events)
    assert RecordingAdapter.durable_contexts == [{}]
    assert RecordingAdapter.stream_kwargs[0]["resume"] is True


@pytest.mark.asyncio
async def test_v2_shadow_capture_failure_is_content_free_and_does_not_block_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)

    def fail_capture(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("private plan failure detail")

    monkeypatch.setattr(coding_api.TurnContextAssembler, "prepare_new_turn", fail_capture)

    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="private user request",
            run_id="run-shadow-failure",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
        )
    ]

    shadow_error = next(
        event for event in events if event.payload.get("type") == "turn_context_plan_shadow_failed"
    )
    assert shadow_error.status == "error"
    assert shadow_error.payload["error_code"] == "capture_failed"
    assert "private" not in str(shadow_error.payload)
    assert events[-2].payload["type"] == "final"
    assert (
        TurnPlanStore(runtime.storage_root, runtime.session_id).load_for_run("run-shadow-failure")
        is None
    )


@pytest.mark.asyncio
async def test_v2_a1_mismatch_is_audit_event_and_does_not_block_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)

    def report_mismatch(plan: Any, request: Any) -> TurnContextComparison:
        del request
        return TurnContextComparison(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            run_id=plan.run_id,
            checked_codes=("prompt.rendered_hash",),
            mismatch_codes=("prompt.rendered_hash",),
        )

    monkeypatch.setattr(coding_api, "compare_turn_context_plan", report_mismatch)
    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="a1 mismatch request",
            run_id="run-a1-mismatch",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
        )
    ]

    comparison = next(
        event for event in events if event.payload.get("type") == "turn_context_plan_compared"
    )
    assert comparison.status == "error"
    assert comparison.payload["matched"] is False
    assert comparison.payload["mismatch_codes"] == ["prompt.rendered_hash"]
    assert events[-2].payload["type"] == "final"
    assert RecordingAdapter.stream_kwargs


@pytest.mark.asyncio
async def test_v2_a1_receipt_is_mirrored_to_run_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)

    events = [
        event
        async for event in coding_api._runtime_timeline_events(
            runtime,
            content="trace receipt request",
            skill_prompt=None,
            command="",
            arguments="",
            run_id="run-a1-trace",
            surface_context={"surface": "coding"},
            harness_checkpointer=object(),
            mcp_catalog=None,
        )
    ]

    assert any(event.payload.get("type") == "turn_context_plan_compared" for event in events)
    trace = runtime.run_store.get_run("run-a1-trace")["events"]
    trace_event = next(item for item in trace if item.get("type") == "turn_context_plan_compared")
    assert trace_event["matched"] is True
    assert "trace receipt request" not in json.dumps(trace_event, ensure_ascii=False)


@pytest.mark.asyncio
async def test_v2_context_assembly_off_keeps_legacy_path_without_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)

    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="legacy path",
            run_id="run-plan-off",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            context_assembly_mode="off",
        )
    ]

    assert not any(
        str(event.payload.get("type", "")).startswith("turn_context_plan") for event in events
    )
    assert events[-2].payload["type"] == "final"
    assert (
        TurnPlanStore(runtime.storage_root, runtime.session_id).load_for_run("run-plan-off") is None
    )


@pytest.mark.asyncio
async def test_v2_enforce_binds_the_verified_plan_before_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)

    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="enforced request",
            run_id="run-enforced",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            context_assembly_mode="enforce",
        )
    ]

    plan = TurnPlanStore(runtime.storage_root, runtime.session_id).load_for_run("run-enforced")
    assert plan is not None
    assert RecordingAdapter.init_kwargs["model_context_frame"] is not None
    assert RecordingAdapter.init_kwargs["system_prompt"] is None
    assert RecordingAdapter.stream_kwargs[0]["turn_context_plan"] == {
        "version": 1,
        "run_id": "run-enforced",
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
    }
    assert any(event.payload.get("type") == "turn_context_plan_compared" for event in events)
    assert events[-1].status == "completed"


@pytest.mark.asyncio
async def test_v2_enforce_capture_failure_stops_before_adapter_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)

    def fail_capture(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("private capture detail")

    def fail_if_adapter_created(**kwargs: Any) -> None:
        del kwargs
        raise AssertionError("adapter must not be created after enforce capture failure")

    monkeypatch.setattr(coding_api.TurnContextAssembler, "prepare_new_turn", fail_capture)
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", fail_if_adapter_created)

    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="private request",
            run_id="run-enforce-capture-failed",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            context_assembly_mode="enforce",
        )
    ]

    assert events[-2].payload["type"] == "turn_context_plan_enforcement_failed"
    assert events[-2].payload["error_code"] == "context_plan_capture_failed"
    assert "private" not in json.dumps(events[-2].payload)
    assert events[-1].payload["error_type"] == "context_plan_capture_failed"


@pytest.mark.asyncio
async def test_v2_enforce_mismatch_stops_before_adapter_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)

    def report_mismatch(plan: Any, request: Any) -> TurnContextComparison:
        del request
        return TurnContextComparison(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            run_id=plan.run_id,
            checked_codes=("tools.catalog_hash",),
            mismatch_codes=("tools.catalog_hash",),
        )

    def fail_if_adapter_created(**kwargs: Any) -> None:
        del kwargs
        raise AssertionError("adapter must not be created after enforce mismatch")

    monkeypatch.setattr(coding_api, "compare_turn_context_plan", report_mismatch)
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", fail_if_adapter_created)

    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="mismatch request",
            run_id="run-enforce-mismatch",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            context_assembly_mode="enforce",
        )
    ]

    assert events[-2].payload["error_code"] == "context_plan_mismatch"
    assert events[-1].payload["error_type"] == "context_plan_mismatch"


@pytest.mark.asyncio
async def test_v2_enforce_resume_requires_a_stored_plan_before_adapter_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)

    def fail_if_adapter_created(**kwargs: Any) -> None:
        del kwargs
        raise AssertionError("adapter must not be created without a resume plan")

    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", fail_if_adapter_created)
    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="resume original request",
            run_id="run-missing-plan",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            resume_value={"interrupt-1": {"choice": "once"}},
            resume_attempt=1,
            context_assembly_mode="enforce",
        )
    ]

    assert events[-2].payload["error_code"] == "resume_plan_missing"
    assert events[-1].payload["error_type"] == "resume_plan_missing"


@pytest.mark.asyncio
async def test_v2_enforce_resume_uses_plan_routing_instead_of_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)
    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="1 + 1 等于多少？",
            run_id="run-plan-resume",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            context_assembly_mode="enforce",
        )
    ]
    plan = TurnPlanStore(runtime.storage_root, runtime.session_id).load_for_run("run-plan-resume")
    assert plan is not None
    runtime.session["history"] = runtime.session["history"][:-1]
    binding = {
        "version": 1,
        "run_id": plan.run_id,
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
    }
    SessionEventJournal(runtime.storage_root, runtime.session_id).append(
        run_id="run-plan-resume",
        kind="harness",
        status="completed",
        payload={
            "type": "retrieval_gate_decided",
            "selected_sources": ["web"],
            "tool_scope": "retrieval_only",
        },
        event_id="forged:timeline:gate",
    )
    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="1 + 1 等于多少？",
            run_id="run-plan-resume",
            surface_context=None,
            thread_goal=None,
            checkpointer=StaticPlanCheckpoint(runtime=runtime, binding=binding),
            mcp_catalog=None,
            resume_value={"interrupt-1": {"choice": "once"}},
            resume_attempt=1,
            context_assembly_mode="enforce",
        )
    ]

    deferred_setup = RecordingAdapter.init_kwargs["deferred_setup"]
    assert "search_web" not in deferred_setup.deferred_names
    assert RecordingAdapter.stream_kwargs[0]["turn_context_plan"] == binding
    assert any(
        event.payload.get("type") == "turn_context_plan_resume_compared"
        and event.payload.get("matched") is True
        for event in events
    ), [event.payload for event in events]


@pytest.mark.asyncio
async def test_v2_enforce_resume_rejects_capability_catalog_drift_before_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)
    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="1 + 1 等于多少？",
            run_id="run-catalog-drift",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            context_assembly_mode="enforce",
        )
    ]
    plan = TurnPlanStore(runtime.storage_root, runtime.session_id).load_for_run("run-catalog-drift")
    assert plan is not None
    runtime.session["history"] = runtime.session["history"][:-1]
    binding = {
        "version": 1,
        "run_id": plan.run_id,
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
    }

    def fail_if_adapter_created(**kwargs: Any) -> None:
        del kwargs
        raise AssertionError("adapter must not be created after catalog drift")

    original_builder = coding_api.build_deerflow_coding_tool_bundle

    def build_drifted_bundle(*args: Any, **kwargs: Any) -> Any:
        bundle = original_builder(*args, **kwargs)
        return replace(
            bundle,
            deferred_setup=replace(
                bundle.deferred_setup,
                catalog_hash="catalog-drifted",
            ),
        )

    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", fail_if_adapter_created)
    monkeypatch.setattr(coding_api, "build_deerflow_coding_tool_bundle", build_drifted_bundle)
    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="1 + 1 等于多少？",
            run_id="run-catalog-drift",
            surface_context=None,
            thread_goal=None,
            checkpointer=StaticPlanCheckpoint(runtime=runtime, binding=binding),
            mcp_catalog=None,
            resume_value={"interrupt-1": {"choice": "once"}},
            resume_attempt=1,
            context_assembly_mode="enforce",
        )
    ]

    comparison = next(
        event
        for event in events
        if event.payload.get("type") == "turn_context_plan_resume_compared"
    )
    assert comparison.payload["matched"] is False
    assert "tools.catalog_hash" in comparison.payload["mismatch_codes"]
    assert events[-2].payload["error_code"] == "resume_plan_dependency_mismatch"
    assert events[-1].payload["error_type"] == "resume_plan_dependency_mismatch"


@pytest.mark.asyncio
async def test_v2_resume_mcp_revision_drift_stops_before_discovery_sandbox_and_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    transport = CountingMcpTransport()
    manager = McpManager(
        McpConfigSnapshot(
            revision="mcp-r1",
            servers=(McpServerConfig(name="docs", transport="stdio"),),
        ),
        transport,
    )
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)
    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="capture MCP lifecycle",
            run_id="run-mcp-lifecycle-drift",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=manager,
            context_assembly_mode="enforce",
        )
    ]
    plan = TurnPlanStore(runtime.storage_root, runtime.session_id).load_for_run(
        "run-mcp-lifecycle-drift"
    )
    assert plan is not None
    binding = plan.checkpoint_binding()
    assert transport.discoveries == 1
    runtime.session["history"] = runtime.session["history"][:-1]
    await manager.replace_snapshot(
        McpConfigSnapshot(
            revision="mcp-r2",
            servers=(McpServerConfig(name="docs", transport="stdio"),),
        )
    )

    def fail_if_adapter_created(**kwargs: Any) -> None:
        del kwargs
        raise AssertionError("MCP lifecycle drift must stop before adapter creation")

    def fail_if_sandbox_created(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("MCP lifecycle drift must stop before sandbox creation")

    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", fail_if_adapter_created)
    monkeypatch.setattr(coding_api, "create_coding_sandbox", fail_if_sandbox_created)
    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="capture MCP lifecycle",
            run_id="run-mcp-lifecycle-drift",
            surface_context=None,
            thread_goal=None,
            checkpointer=StaticPlanCheckpoint(runtime=runtime, binding=binding),
            mcp_catalog=manager,
            resume_value={"interrupt-1": {"choice": "once"}},
            resume_attempt=1,
            context_assembly_mode="enforce",
        )
    ]

    assert transport.discoveries == 1
    assert events[-2].payload["error_code"] == "mcp_config_revision_mismatch"
    assert events[-1].payload["error_type"] == "mcp_config_revision_mismatch"


@pytest.mark.asyncio
async def test_v2_resume_missing_mcp_lifecycle_stops_before_discovery_sandbox_and_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    transport = CountingMcpTransport()
    manager = McpManager(
        McpConfigSnapshot(
            revision="mcp-r1",
            servers=(McpServerConfig(name="docs", transport="stdio"),),
        ),
        transport,
    )
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)
    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="capture MCP lifecycle",
            run_id="run-mcp-lifecycle-missing",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=manager,
            context_assembly_mode="enforce",
        )
    ]
    plan = TurnPlanStore(runtime.storage_root, runtime.session_id).load_for_run(
        "run-mcp-lifecycle-missing"
    )
    assert plan is not None
    payload = plan.to_payload()
    tools = payload["tools"]
    assert isinstance(tools, dict)
    tools.pop("mcp_lifecycle")
    legacy = TurnContextPlan.create(
        **plan.identity_kwargs(),
        created_at=plan.created_at,
        admission=payload["admission"],
        prompt=payload["prompt"],
        context_refs=payload["context_refs"],
        retrieval=payload["retrieval"],
        tools=tools,
        execution=payload["execution"],
        resume=payload["resume"],
        budget=payload.get("budget"),
    )
    monkeypatch.setattr(
        TurnPlanStore,
        "load_for_run",
        lambda self, run_id: legacy if run_id == legacy.run_id else None,
    )
    runtime.session["history"] = runtime.session["history"][:-1]
    binding = legacy.checkpoint_binding()

    def fail_if_adapter_created(**kwargs: Any) -> None:
        del kwargs
        raise AssertionError("missing MCP lifecycle must stop before adapter creation")

    def fail_if_sandbox_created(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("missing MCP lifecycle must stop before sandbox creation")

    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", fail_if_adapter_created)
    monkeypatch.setattr(coding_api, "create_coding_sandbox", fail_if_sandbox_created)
    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="capture MCP lifecycle",
            run_id=legacy.run_id,
            surface_context=None,
            thread_goal=None,
            checkpointer=StaticPlanCheckpoint(runtime=runtime, binding=binding),
            mcp_catalog=manager,
            resume_value={"interrupt-1": {"choice": "once"}},
            resume_attempt=1,
            context_assembly_mode="enforce",
        )
    ]

    assert transport.discoveries == 1
    assert events[-2].payload["error_code"] == "resume_mcp_lifecycle_missing"
    assert events[-1].payload["error_type"] == "resume_mcp_lifecycle_missing"


@pytest.mark.asyncio
async def test_v2_resume_skill_catalog_drift_stops_before_sandbox_and_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    skill_dir = runtime.workspace.root / "skills" / "review"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: review\nallowed-tools: read_file\n---\nReview v1.",
        encoding="utf-8",
    )
    runtime.skill_registry = SkillRegistry(root=runtime.workspace.root, home=tmp_path / "home")
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)
    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="/review inspect",
            run_id="run-skill-lifecycle-drift",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            context_assembly_mode="enforce",
        )
    ]
    plan = TurnPlanStore(runtime.storage_root, runtime.session_id).load_for_run(
        "run-skill-lifecycle-drift"
    )
    assert plan is not None
    binding = plan.checkpoint_binding()
    runtime.session["history"] = runtime.session["history"][:-1]
    skill_file.write_text(
        "---\nname: review\nallowed-tools: read_file, search\n---\nReview v2.",
        encoding="utf-8",
    )
    runtime.skill_registry = SkillRegistry(root=runtime.workspace.root, home=tmp_path / "home")

    def fail_if_adapter_created(**kwargs: Any) -> None:
        del kwargs
        raise AssertionError("Skill lifecycle drift must stop before adapter creation")

    def fail_if_sandbox_created(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise AssertionError("Skill lifecycle drift must stop before sandbox creation")

    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", fail_if_adapter_created)
    monkeypatch.setattr(coding_api, "create_coding_sandbox", fail_if_sandbox_created)
    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="/review inspect",
            run_id="run-skill-lifecycle-drift",
            surface_context=None,
            thread_goal=None,
            checkpointer=StaticPlanCheckpoint(runtime=runtime, binding=binding),
            mcp_catalog=None,
            resume_value={"interrupt-1": {"choice": "once"}},
            resume_attempt=1,
            context_assembly_mode="enforce",
        )
    ]

    assert events[-2].payload["error_code"] == "skill_catalog_revision_mismatch"
    assert events[-1].payload["error_type"] == "skill_catalog_revision_mismatch"


@pytest.mark.asyncio
async def test_v2_enforce_resume_rejects_checkpoint_without_plan_binding_before_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)
    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="checkpoint binding request",
            run_id="run-binding-missing",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            context_assembly_mode="enforce",
        )
    ]
    plan = TurnPlanStore(runtime.storage_root, runtime.session_id).load_for_run(
        "run-binding-missing"
    )
    assert plan is not None
    runtime.session["history"] = runtime.session["history"][:-1]

    def fail_if_adapter_created(**kwargs: Any) -> None:
        del kwargs
        raise AssertionError("adapter must not be created without checkpoint binding")

    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", fail_if_adapter_created)
    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="checkpoint binding request",
            run_id="run-binding-missing",
            surface_context=None,
            thread_goal=None,
            checkpointer=StaticPlanCheckpoint(runtime=runtime, binding={}),
            mcp_catalog=None,
            resume_value={"interrupt-1": {"choice": "once"}},
            resume_attempt=1,
            context_assembly_mode="enforce",
        )
    ]

    assert events[-2].payload["error_code"] == "resume_plan_checkpoint_mismatch"
    assert events[-1].payload["error_type"] == "resume_plan_checkpoint_mismatch"


@pytest.mark.asyncio
async def test_v2_book_learning_synthesis_finishes_without_running_parent_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)

    class FakeCoordinator:
        calls = 0

        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        async def run(self, request: object) -> BookLearningCoordinatorOutcome:
            del request
            type(self).calls += 1
            return BookLearningCoordinatorOutcome(
                activated=True,
                decision="answer",
                stop_reason="evidence_sufficient",
                retrieval_rounds=2,
                child_run_ids=("research-1", "research-2", "synthesize-1"),
                evidence_refs=("kcite_a", "kcite_b"),
                final_answer="综合结论 [kcite_a] [kcite_b]",
                context={"decision": "answer", "evidence": []},
                public_events=(
                    {"type": "retrieval_sufficiency_assessed", "decision": "answer"},
                    {"type": "agentic_rag_completed", "decision": "answer"},
                ),
            )

    def fail_if_created(**kwargs: Any) -> None:
        del kwargs
        raise AssertionError("synthesized answer must bypass the parent adapter")

    monkeypatch.setattr(coding_api, "BookLearningCoordinator", FakeCoordinator)
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", fail_if_created)
    monkeypatch.setattr(
        coding_api,
        "CodingKnowledgePort",
        lambda runtime: AvailableKnowledgePort(),
    )

    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="比较两本书对分工的解释",
            run_id="run-agentic-book",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
        )
    ]

    assert FakeCoordinator.calls == 1
    assert events[-2].payload["content"] == "综合结论 [kcite_a] [kcite_b]"
    assert events[-1].payload["route"] == "book_learning"
    assert any(event.payload.get("type") == "agentic_rag_completed" for event in events)


@pytest.mark.asyncio
async def test_v2_resume_does_not_repeat_book_learning_coordinator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime

    class FailCoordinator:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs
            raise AssertionError("resume must not reconstruct the book-learning coordinator")

    monkeypatch.setattr(coding_api, "BookLearningCoordinator", FailCoordinator)
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)

    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="resume original request",
            run_id="run-resume-book",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            resume_value={"interrupt-1": {"approval_id": "approval-1", "choice": "once"}},
            resume_attempt=1,
        )
    ]

    assert RecordingAdapter.stream_kwargs[0]["resume"] is True


@pytest.mark.asyncio
async def test_v2_book_learning_abstention_bypasses_parent_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)

    class AbstainingCoordinator:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        async def run(self, request: object) -> BookLearningCoordinatorOutcome:
            del request
            return BookLearningCoordinatorOutcome(
                activated=True,
                decision="abstain",
                stop_reason="no_new_evidence",
                retrieval_rounds=2,
                final_answer="证据不足，暂不作答。",
                public_events=({"type": "agentic_rag_completed", "decision": "abstain"},),
            )

    def fail_if_created(**kwargs: Any) -> None:
        del kwargs
        raise AssertionError("abstention must bypass the parent adapter")

    monkeypatch.setattr(coding_api, "BookLearningCoordinator", AbstainingCoordinator)
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", fail_if_created)
    monkeypatch.setattr(
        coding_api,
        "CodingKnowledgePort",
        lambda runtime: AvailableKnowledgePort(),
    )

    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="比较两本书但没有足够证据",
            run_id="run-agentic-abstain",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
        )
    ]

    assert events[-2].payload["content"] == "证据不足，暂不作答。"
    assert events[-1].payload["route"] == "book_learning"


@pytest.mark.asyncio
async def test_v2_direct_book_evidence_is_injected_into_parent_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime

    class DirectCoordinator:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        async def run(self, request: object) -> BookLearningCoordinatorOutcome:
            del request
            return BookLearningCoordinatorOutcome(
                activated=True,
                decision="answer",
                stop_reason="evidence_sufficient",
                retrieval_rounds=1,
                evidence_refs=("kcite_direct",),
                context={
                    "decision": "answer",
                    "round_index": 1,
                    "evidence": [
                        {"citation_id": "kcite_direct", "content": "书籍中的直接证据"},
                    ],
                },
                public_events=({"type": "agentic_rag_completed", "decision": "answer"},),
            )

    monkeypatch.setattr(coding_api, "BookLearningCoordinator", DirectCoordinator)
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)
    monkeypatch.setattr(
        coding_api,
        "CodingKnowledgePort",
        lambda runtime: AvailableKnowledgePort(),
    )

    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="解释这本书里的分工概念",
            run_id="run-direct-book",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
        )
    ]

    book_context = RecordingAdapter.durable_contexts[0]["book_learning"]
    assert book_context["evidence"][0]["citation_id"] == "kcite_direct"
    assert RecordingAdapter.stream_kwargs[0]["resume"] is False


@pytest.mark.asyncio
async def test_v2_external_resume_restores_gate_tool_filter_from_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)
    SessionEventJournal(runtime.storage_root, runtime.session_id).append(
        run_id="run-resume-skip",
        kind="harness",
        status="completed",
        payload={
            "type": "retrieval_gate_decided",
            "selected_sources": [],
        },
        event_id="harness:run-resume-skip:retrieval-gate",
    )

    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="resume original request",
            run_id="run-resume-skip",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            web_search_port=AvailableWebSearchPort(),  # type: ignore[arg-type]
            resume_value={"interrupt-1": {"approval_id": "approval-1", "choice": "once"}},
            resume_attempt=1,
        )
    ]

    deferred_setup = RecordingAdapter.init_kwargs["deferred_setup"]
    assert "search_web" not in deferred_setup.deferred_names


@pytest.mark.asyncio
async def test_v2_external_resume_restores_web_only_research_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)
    SessionEventJournal(runtime.storage_root, runtime.session_id).append(
        run_id="run-resume-web",
        kind="harness",
        status="completed",
        payload={
            "type": "retrieval_gate_decided",
            "version": 1,
            "selected_sources": ["web"],
        },
        event_id="harness:run-resume-web:retrieval-gate",
    )

    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="resume original request",
            run_id="run-resume-web",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            web_search_port=AvailableWebSearchPort(),  # type: ignore[arg-type]
            resume_value={"interrupt-1": {"approval_id": "approval-1", "choice": "once"}},
            resume_attempt=1,
        )
    ]

    deferred_setup = RecordingAdapter.init_kwargs["deferred_setup"]
    config = RecordingAdapter.init_kwargs["subagent_tool_config"]
    assert "search_web" in deferred_setup.deferred_names
    assert config.resolve("research").tool_scope == (
        "list_files",
        "read_file",
        "search",
        "search_web",
    )


@pytest.mark.asyncio
async def test_v2_external_resume_restores_knowledge_only_research_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    RecordingAdapter.runtime = runtime
    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", RecordingAdapter)
    monkeypatch.setattr(
        coding_api,
        "CodingKnowledgePort",
        lambda runtime: AvailableKnowledgePort(),
    )
    SessionEventJournal(runtime.storage_root, runtime.session_id).append(
        run_id="run-resume-knowledge",
        kind="harness",
        status="completed",
        payload={
            "type": "retrieval_gate_decided",
            "version": 1,
            "selected_sources": ["knowledge"],
        },
        event_id="harness:run-resume-knowledge:retrieval-gate",
    )

    _ = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="resume original request",
            run_id="run-resume-knowledge",
            surface_context={"surface": "coding"},
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
            resume_value={"interrupt-1": {"approval_id": "approval-1", "choice": "once"}},
            resume_attempt=1,
        )
    ]

    config = RecordingAdapter.init_kwargs["subagent_tool_config"]
    assert config.resolve("research").tool_scope == (
        "list_files",
        "read_file",
        "search",
        "knowledge_search",
    )


@pytest.mark.asyncio
async def test_v2_emergency_context_blocks_graph_model_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path, controller=EmergencyContextController())

    def fail_if_created(**kwargs: Any) -> None:
        del kwargs
        raise AssertionError("graph adapter must not be created during context emergency")

    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", fail_if_created)

    events = [
        event
        async for event in coding_api._deerflow_timeline_events(
            runtime,
            content="new request",
            run_id="run-emergency",
            surface_context=None,
            thread_goal=None,
            checkpointer=object(),
            mcp_catalog=None,
        )
    ]

    assert [event.kind for event in events][-2:] == ["system", "terminal"]
    assert events[-1].status == "error"
    assert events[-1].payload["error_type"] == "context_emergency"
    assert not any(event.payload.get("type") == "turn_context_plan_prepared" for event in events)
    assert runtime.active_run_id is None
    assert [item["role"] for item in runtime.session["history"][-1:]] == ["user"]


@pytest.mark.asyncio
async def test_v2_cancellation_releases_runtime_context_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _runtime(tmp_path)
    entered = asyncio.Event()

    class BlockingAdapter:
        def __init__(self, **kwargs: Any) -> None:
            del kwargs

        async def stream_turn(self, **kwargs: Any):  # type: ignore[no-untyped-def]
            del kwargs
            entered.set()
            await asyncio.Event().wait()
            yield  # pragma: no cover

    monkeypatch.setattr(coding_api, "SageHarnessRuntimeAdapter", BlockingAdapter)

    async def consume() -> list[RunEvent]:
        return [
            event
            async for event in coding_api._deerflow_timeline_events(
                runtime,
                content="cancel me",
                run_id="run-cancel",
                surface_context=None,
                thread_goal=None,
                checkpointer=object(),
                mcp_catalog=None,
            )
        ]

    task = asyncio.create_task(consume())
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert runtime.active_run_id == "run-cancel"
    assert runtime.context_snapshot()["context_operation_active"] is True

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.active_run_id is None
    assert runtime.context_snapshot()["context_operation_active"] is False
