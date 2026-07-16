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

    def before_model_request(
        self, history: list[dict[str, Any]], **kwargs: Any
    ) -> PreparedContext:
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

    def before_model_request(
        self, history: list[dict[str, Any]], **kwargs: Any
    ) -> PreparedContext:
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

    def __init__(self, **kwargs: Any) -> None:
        del kwargs
        type(self).durable_contexts = []

    async def stream_turn(self, **kwargs: Any):  # type: ignore[no-untyped-def]
        assert type(self).runtime.active_run_id == kwargs["run_id"]
        type(self).durable_contexts.append(dict(kwargs["durable_context"]))
        yield RunEvent(
            kind="assistant",
            status="running",
            payload={"type": "text_delta", "delta": "graph answer"},
        )


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
    assert runtime.session["context_state"]["checkpoint_id"] == "compact-v2"
    assert runtime.active_run_id is None
    assert runtime.context_snapshot()["context_operation_active"] is False
    assert [item["role"] for item in runtime.session["history"][-2:]] == [
        "user",
        "assistant",
    ]


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
