"""Concurrency contract for turn execution and manual compaction."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.coding.context import ContextPolicy
from core.coding.runtime import CodingRuntime


class Model:
    async def complete(self, prompt: str) -> str:
        del prompt
        await asyncio.sleep(0)
        return "<final>done</final>"


@pytest.mark.asyncio
async def test_run_turn_rejects_when_manual_context_operation_holds_lock(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = CodingRuntime(
        session_id="s-lock",
        workspace_root=workspace,
        model=Model(),
        storage_root=tmp_path / ".coding",
        context_policy=ContextPolicy(context_window_tokens=100_000, output_reserve_tokens=10_000),
    )
    await runtime._context_operation_lock.acquire()
    try:
        events = [event async for event in runtime.run_turn("hello")]
    finally:
        runtime._context_operation_lock.release()
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert events[0]["run_id"] == ""
    assert events[0]["message"] == (
        "A context operation is already in progress for this session"
    )
    assert runtime.active_run_id is None
    assert runtime.run_store.list_runs() == []
