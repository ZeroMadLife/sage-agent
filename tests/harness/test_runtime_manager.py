"""Checkpoint command plumbing for the reusable harness runtime."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sage_harness.config import HarnessRunContext
from sage_harness.runtime.checkpoint import load_scoped_checkpoint
from sage_harness.runtime.manager import HarnessRunManager, HarnessRunRequest
from typing_extensions import TypedDict


class RecordingGraph:
    def __init__(self) -> None:
        self.inputs: list[Any] = []

    async def astream(self, input, *, config, context, stream_mode):  # type: ignore[no-untyped-def]
        self.inputs.append(input)
        yield ("custom", {"type": "agent_started"})


class RecursionFailureGraph:
    async def astream(self, input, *, config, context, stream_mode):  # type: ignore[no-untyped-def]
        _ = input, config, context, stream_mode
        if False:  # pragma: no cover - keeps this an async generator
            yield None
        raise GraphRecursionError("private recursion details")


class SlowGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def astream(self, input, *, config, context, stream_mode):  # type: ignore[no-untyped-def]
        _ = input, config, context, stream_mode
        self.started.set()
        await asyncio.sleep(30)
        yield ("custom", {"type": "too_late"})


class InnerTimeoutGraph:
    async def astream(self, input, *, config, context, stream_mode):  # type: ignore[no-untyped-def]
        _ = input, config, context, stream_mode
        if False:  # pragma: no cover - keeps this an async generator
            yield None
        raise TimeoutError("provider timeout")


class StaticCheckpointSaver:
    def __init__(self, checkpoint: object) -> None:
        self.checkpoint = checkpoint

    async def aget_tuple(self, config):  # type: ignore[no-untyped-def]
        _ = config
        return self.checkpoint


def _context(run_id: str) -> HarnessRunContext:
    return HarnessRunContext(
        thread_id="thread-1",
        run_id=run_id,
        owner_id="owner-1",
        workspace_id="workspace-1",
        workspace_path="/tmp/workspace",
    )


def _request(*, resume: bool = False) -> HarnessRunRequest:
    return HarnessRunRequest(
        thread_id="thread-1",
        run_id="run-1",
        context=_context("run-1"),
        message="" if resume else "continue",
        resume=resume,
        resume_value={"choice": "once"} if resume else None,
    )


def test_manager_uses_server_owned_command_for_checkpoint_resume() -> None:
    graph = RecordingGraph()

    async def run() -> None:
        items = [item async for item in HarnessRunManager(graph).stream(_request(resume=True))]
        assert items[0].mode == "custom"

    asyncio.run(run())

    command = graph.inputs[0]
    assert command.__class__.__name__ == "Command"
    assert command.resume == {"choice": "once"}


def test_resume_request_can_omit_a_new_user_message() -> None:
    request = _request(resume=True)
    assert request.message == ""


def test_path_only_legacy_checkpoint_can_be_claimed_by_matching_scope() -> None:
    legacy = SimpleNamespace(
        checkpoint={
            "channel_values": {
                "thread_data": {"workspace_path": "/tmp/workspace"},
            }
        }
    )

    async def run() -> object:
        return await load_scoped_checkpoint(StaticCheckpointSaver(legacy), _context("run-1"))  # type: ignore[arg-type]

    assert asyncio.run(run()) is legacy


def test_request_rejects_a_cross_run_context() -> None:
    try:
        HarnessRunRequest(
            thread_id="thread-1",
            run_id="run-2",
            context=_context("run-1"),
            message="continue",
        )
    except ValueError as exc:
        assert str(exc) == "run context run_id does not match request"
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("cross-run context must be rejected")


class _InterruptState(TypedDict, total=False):
    decision: str


def test_manager_resumes_a_real_checkpointed_graph_interrupt() -> None:
    def wait_for_decision(state: _InterruptState) -> _InterruptState:
        decision = interrupt({"type": "approval_required", "tool": "write_file"})
        return {"decision": str(decision)}

    graph = StateGraph(_InterruptState)
    graph.add_node("wait", wait_for_decision)
    graph.add_edge(START, "wait")
    graph.add_edge("wait", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    manager = HarnessRunManager(compiled)

    async def run() -> tuple[list[Any], list[Any]]:
        first = [
            item
            async for item in manager.stream(
                HarnessRunRequest(
                    thread_id="thread-1",
                    run_id="run-1",
                    context=_context("run-1"),
                    message="start",
                )
            )
        ]
        resumed = [
            item
            async for item in manager.stream(
                HarnessRunRequest(
                    thread_id="thread-1",
                    run_id="run-2",
                    context=_context("run-2"),
                    message="",
                    resume=True,
                    resume_value="once",
                )
            )
        ]
        return first, resumed

    first, resumed = asyncio.run(run())
    first_values = [item.payload for item in first if item.mode == "values"]
    resumed_values = [item.payload for item in resumed if item.mode == "values"]
    assert any("__interrupt__" in value for value in first_values)
    assert resumed_values[-1]["decision"] == "once"


def test_manager_recovers_recursion_limit_as_a_public_budget_stop() -> None:
    request = HarnessRunRequest(
        thread_id="thread-1",
        run_id="run-1",
        context=_context("run-1"),
        message="continue",
        recursion_limit=2,
    )

    async def run() -> list[Any]:
        return [item async for item in HarnessRunManager(RecursionFailureGraph()).stream(request)]

    items = asyncio.run(run())

    assert len(items) == 1
    assert items[0].mode == "custom"
    assert items[0].payload == {
        "type": "run_budget_exhausted",
        "stop_reason": "step_capped",
        "used": 2,
        "limit": 2,
        "notice": "本轮已达到执行步数安全上限，已停止继续执行。",
    }
    assert "private recursion details" not in str(items[0].payload)


def test_manager_recovers_active_invocation_timeout_as_a_public_budget_stop() -> None:
    request = HarnessRunRequest(
        thread_id="thread-1",
        run_id="run-1",
        context=_context("run-1"),
        message="continue",
        timeout_seconds=0.01,
    )

    async def run() -> list[Any]:
        return [item async for item in HarnessRunManager(SlowGraph()).stream(request)]

    items = asyncio.run(run())

    assert len(items) == 1
    assert items[0].payload["stop_reason"] == "time_capped"
    assert items[0].payload["limit"] == 0.01


def test_manager_does_not_swallow_external_cancellation() -> None:
    graph = SlowGraph()

    async def run() -> None:
        async def consume() -> None:
            _ = [item async for item in HarnessRunManager(graph).stream(_request())]

        task = asyncio.create_task(consume())
        await graph.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_manager_does_not_reclassify_an_inner_timeout_as_run_budget() -> None:
    async def run() -> None:
        with pytest.raises(TimeoutError, match="provider timeout"):
            _ = [item async for item in HarnessRunManager(InnerTimeoutGraph()).stream(_request())]

    asyncio.run(run())
