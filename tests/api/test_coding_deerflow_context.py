"""Turn-boundary context integration for the DeerFlow runtime profile."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

import pytest

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
from core.coding.run_coordinator import RunEvent
from core.coding.runtime import CodingRuntime


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
    assert RecordingAdapter.durable_contexts == [{}]
    assert RecordingAdapter.stream_kwargs[0]["resume"] is True


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
