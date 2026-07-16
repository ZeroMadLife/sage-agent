"""Sage application adapter tests for awaited read-only children."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sage_harness import SubagentRequest

from core.coding.memory import workspace_id_from_path
from core.coding.runtime import CodingRuntime
from core.harness.subagent_adapter import CodingSubagentExecutor


class FakeModel:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls = 0

    async def complete(self, prompt: str) -> str:
        _ = prompt
        self.calls += 1
        return self.responses.pop(0)


class SlowModel:
    async def complete(self, prompt: str) -> str:
        _ = prompt
        await asyncio.sleep(30)
        return "<final>late</final>"


def _runtime(tmp_path: Path, model: object) -> CodingRuntime:
    runtime = CodingRuntime(
        session_id="session-parent",
        workspace_root=tmp_path,
        model=model,
        model_factory=lambda: model,
        storage_root=tmp_path / ".coding",
        runtime_profile="deerflow_v2",
    )
    runtime.active_run_id = "run-parent"
    return runtime


def _request(tmp_path: Path, child_run_id: str = "child_test") -> SubagentRequest:
    return SubagentRequest(
        parent_thread_id="session-parent",
        parent_run_id="run-parent",
        child_run_id=child_run_id,
        description="inspect README",
        prompt="Read README.md and report its first line.",
        subagent_type="Explore",
        workspace_id=workspace_id_from_path(tmp_path),
        workspace_path=str(tmp_path),
        tool_scope=("list_files", "read_file", "search"),
        token_budget=10_000,
        timeout_seconds=10,
        max_steps=8,
    )


def test_coding_subagent_executes_read_only_and_replays_terminal_trace(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    model = FakeModel(
        [
            '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
            "<final>The first line is # Sage.</final>",
        ]
    )
    runtime = _runtime(tmp_path, model)
    executor = CodingSubagentExecutor(runtime)
    request = _request(tmp_path)

    first = asyncio.run(executor.execute(request))
    second = asyncio.run(executor.execute(request))

    assert first.status == "succeeded"
    assert second == first
    assert model.calls == 2
    assert first.result_ref == "subagent://session-parent/child_test"
    child_run = runtime.run_store.get_run("child_test")
    assert child_run["events"][0]["type"] == "subagent_started"
    assert child_run["events"][-2]["type"] == "subagent_terminal"
    assert child_run["events"][-2]["status"] == "succeeded"
    assert child_run["events"][-1] == {
        "run_id": "child_test",
        "status": "completed",
        "type": "run_finished",
    }


def test_coding_subagent_rejects_write_capability_before_execution(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, FakeModel(["<final>unused</final>"]))
    executor = CodingSubagentExecutor(runtime)
    request = replace(
        _request(tmp_path),
        tool_scope=("read_file", "write_file"),
    )

    with pytest.raises(ValueError, match="read-only scope"):
        asyncio.run(executor.execute(request))

    with pytest.raises(FileNotFoundError):
        runtime.run_store.get_run(request.child_run_id)


def test_coding_subagent_records_parent_cancellation(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, SlowModel())
    executor = CodingSubagentExecutor(runtime)
    request = _request(tmp_path, "child_cancel")

    async def run() -> None:
        execution = asyncio.create_task(executor.execute(request))
        await asyncio.sleep(0.02)
        await executor.cancel(request.child_run_id, "parent_cancelled")
        execution.cancel()
        with pytest.raises(asyncio.CancelledError):
            await execution

    asyncio.run(run())

    child_run = runtime.run_store.get_run(request.child_run_id)
    terminal = next(
        event for event in reversed(child_run["events"]) if event["type"] == "subagent_terminal"
    )
    assert terminal["status"] == "cancelled"
    assert terminal["error_code"] == "parent_cancelled"
