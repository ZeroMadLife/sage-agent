"""Proposal-only memory boundary for the DeerFlow-compatible runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.coding.runtime import CodingRuntime
from core.harness.memory_adapter import CodingMemoryPort


def _runtime(tmp_path: Path, *, session_id: str = "session-memory") -> CodingRuntime:
    return CodingRuntime(
        session_id=session_id,
        workspace_root=tmp_path / "workspace",
        model=object(),
        storage_root=tmp_path / ".coding",
        runtime_profile="deerflow_v2",
    )


def test_memory_port_proposes_idempotently_without_writing_facts(tmp_path: Path) -> None:
    async def run() -> tuple[CodingRuntime, str]:
        runtime = _runtime(tmp_path)
        port = CodingMemoryPort(runtime)
        async with runtime.harness_turn("run-1"):
            first = await port.propose(
                runtime.session_id,
                "run-1",
                "Use SQLite checkpoints as the canonical resume state",
                topic="decisions",
            )
            replay = await port.propose(
                runtime.session_id,
                "run-1",
                "Use SQLite checkpoints as the canonical resume state",
                topic="decisions",
            )
        assert replay.proposal_id == first.proposal_id
        return runtime, first.proposal_id

    runtime, proposal_id = asyncio.run(run())

    proposals = runtime.memory_manager.list_proposals("pending")
    assert [proposal.proposal_id for proposal in proposals] == [proposal_id]
    assert proposals[0].candidates[0].source == "harness_proposal"
    assert proposals[0].candidates[0].source_ref == "run-1"
    assert runtime.memory_manager.memory_store.list_facts() == []
    assert runtime.memory_manager.list_facts() == []

    approved = runtime.memory_manager.approve(proposal_id, expected_revision=0)
    assert approved.status == "approved"
    assert [fact.content for fact in runtime.memory_manager.list_facts()] == [
        "Use SQLite checkpoints as the canonical resume state"
    ]
    references = asyncio.run(
        CodingMemoryPort(runtime).load_context(runtime.session_id, token_budget=256)
    )
    assert references[0].summary == "Use SQLite checkpoints as the canonical resume state"


def test_memory_port_rejects_cross_thread_and_inactive_run(tmp_path: Path) -> None:
    async def run() -> None:
        runtime = _runtime(tmp_path)
        port = CodingMemoryPort(runtime)
        with pytest.raises(PermissionError, match="thread"):
            await port.propose("other-session", "run-1", "never store this")
        with pytest.raises(PermissionError, match="not active"):
            await port.propose(runtime.session_id, "run-1", "never store this")

    asyncio.run(run())
