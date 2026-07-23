"""Proposal-only memory boundary for the DeerFlow-compatible runtime."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sage_harness.middleware.durable_context import _render_durable_context

from core.coding.persistence.session_event_journal import SessionEventJournal
from core.coding.runtime import CodingRuntime
from core.harness.context_adapter import build_deerflow_durable_context
from core.harness.memory_adapter import CodingMemoryPort
from core.harness.web_evidence import estimated_tokens


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


def test_memory_query_returns_relevant_conflicting_approved_facts(tmp_path: Path) -> None:
    async def run():  # type: ignore[no-untyped-def]
        runtime = _runtime(tmp_path)
        runtime.memory_manager.remember(
            "默认使用快速模式回答。",
            source_ref="preference-v1",
        )
        runtime.memory_manager.remember(
            "默认使用深度模式回答。",
            source_ref="preference-v2",
        )
        runtime.memory_manager.remember(
            "项目使用 PostgreSQL 保存知识图谱。",
            source_ref="database-note",
        )
        return await CodingMemoryPort(runtime).query_context(
            runtime.session_id,
            "我之前告诉你的默认回答模式是什么？",
            token_budget_by_source={"semantic_memory": 256},
        )

    result = asyncio.run(run())

    assert len(result.references) == 2
    assert all(ref.metadata["memory_kind"] == "semantic" for ref in result.references)
    assert all(ref.metadata["conflict"] == "true" for ref in result.references)
    assert len({ref.metadata["conflict_group"] for ref in result.references}) == 1
    assert all(ref.metadata["provenance"] == "approved_memory" for ref in result.references)
    assert "PostgreSQL" not in repr(result.references)
    assert result.used_tokens_by_source["semantic_memory"] > 0


def test_memory_query_builds_bounded_episode_from_durable_timeline(tmp_path: Path) -> None:
    async def run():  # type: ignore[no-untyped-def]
        runtime = _runtime(tmp_path)
        journal = SessionEventJournal(runtime.storage_root, runtime.session_id)
        journal.append(
            run_id="run-previous",
            kind="user",
            status="completed",
            payload={"type": "user", "content": "排查 shell 审批恢复失败"},
        )
        journal.append(
            run_id="run-previous",
            kind="tool",
            status="completed",
            payload={
                "type": "tool_result",
                "tool": "read_file",
                "content": '{"citations":[{"citation_id":"cite-shell"}]}',
            },
        )
        journal.append_terminal_once(
            run_id="run-previous",
            status="completed",
            payload={"event": "run_completed"},
        )
        journal.append(
            run_id="run-current",
            kind="user",
            status="completed",
            payload={"type": "user", "content": "本轮不能成为历史 episode"},
        )
        return await CodingMemoryPort(runtime).query_context(
            runtime.session_id,
            "上次 shell 审批恢复做了什么？",
            token_budget_by_source={"episodic_memory": 256},
            current_run_id="run-current",
        )

    result = asyncio.run(run())

    assert len(result.references) == 1
    episode = result.references[0]
    assert episode.memory_id.startswith("episode_")
    assert episode.revision.startswith("sequence:")
    assert episode.metadata["memory_kind"] == "episodic"
    assert episode.metadata["provenance"] == "durable_timeline"
    assert episode.metadata["run_id"] == "run-previous"
    assert episode.metadata["evidence_refs"] == "cite-shell"
    assert episode.metadata["query_fingerprint"]
    assert "排查 shell 审批恢复失败" not in episode.summary
    assert episode.summary == "历史运行结果：completed；工具：read_file；证据：1 项"
    assert "run-current" not in repr(result.references)


def test_memory_query_omits_unrelated_episodes(tmp_path: Path) -> None:
    async def run():  # type: ignore[no-untyped-def]
        runtime = _runtime(tmp_path)
        journal = SessionEventJournal(runtime.storage_root, runtime.session_id)
        journal.append(
            run_id="run-shell",
            kind="user",
            status="completed",
            payload={"type": "user", "content": "排查 shell 审批恢复失败"},
        )
        journal.append_terminal_once(
            run_id="run-shell",
            status="completed",
            payload={"event": "run_completed"},
        )
        return await CodingMemoryPort(runtime).query_context(
            runtime.session_id,
            "数据库迁移方案是什么？",
            token_budget_by_source={"episodic_memory": 256},
        )

    result = asyncio.run(run())

    assert result.references == ()
    assert result.used_tokens_by_source == {"episodic_memory": 0}
    assert result.omitted_count_by_source == {"episodic_memory": 0}


def test_memory_query_fairly_shares_global_reference_cap_between_sources(
    tmp_path: Path,
) -> None:
    async def run():  # type: ignore[no-untyped-def]
        runtime = _runtime(tmp_path)
        journal = SessionEventJournal(runtime.storage_root, runtime.session_id)
        for index in range(18):
            runtime.memory_manager.remember(
                f"共享检索偏好第 {index} 项。",
                source_ref=f"preference-{index}",
            )
            run_id = f"run-{index}"
            journal.append(
                run_id=run_id,
                kind="user",
                status="completed",
                payload={"type": "user", "content": f"共享检索任务第 {index} 项"},
            )
            journal.append_terminal_once(
                run_id=run_id,
                status="completed",
                payload={"event": "run_completed"},
            )
        return await CodingMemoryPort(runtime).query_context(
            runtime.session_id,
            "共享检索",
            token_budget_by_source={
                "semantic_memory": 100_000,
                "episodic_memory": 100_000,
            },
        )

    result = asyncio.run(run())

    kinds = [str(reference.metadata["memory_kind"]) for reference in result.references]
    assert len(result.references) == 32
    assert kinds.count("semantic") == 16
    assert kinds.count("episodic") == 16
    assert result.omitted_count_by_source == {
        "semantic_memory": 2,
        "episodic_memory": 2,
    }
    assert all(value > 0 for value in result.used_tokens_by_source.values())


def test_memory_budget_never_returns_half_of_a_conflict_group(tmp_path: Path) -> None:
    async def run():  # type: ignore[no-untyped-def]
        runtime = _runtime(tmp_path)
        runtime.memory_manager.remember("默认使用快速模式回答。", source_ref="v1")
        runtime.memory_manager.remember("默认使用深度模式回答。", source_ref="v2")
        generous = await CodingMemoryPort(runtime).query_context(
            runtime.session_id,
            "默认回答模式",
            token_budget_by_source={"semantic_memory": 1_000},
        )
        single_cost = generous.used_tokens_by_source["semantic_memory"] // 2
        bounded = await CodingMemoryPort(runtime).query_context(
            runtime.session_id,
            "默认回答模式",
            token_budget_by_source={"semantic_memory": max(1, single_cost)},
        )
        return generous, bounded

    generous, bounded = asyncio.run(run())

    assert len(generous.references) == 2
    assert bounded.references == ()
    assert bounded.used_tokens_by_source["semantic_memory"] == 0
    assert bounded.omitted_count_by_source["semantic_memory"] == 2


def test_memory_receipt_accounts_for_rendered_metadata_and_chinese(tmp_path: Path) -> None:
    async def run():  # type: ignore[no-untyped-def]
        runtime = _runtime(tmp_path)
        runtime.memory_manager.remember(
            "用户偏好先给出中文结论，再列出可验证证据。",
            source_ref="preference-with-provenance",
        )
        return await CodingMemoryPort(runtime).query_context(
            runtime.session_id,
            "我的偏好是什么？",
            token_budget_by_source={"semantic_memory": 1_000},
        )

    result = asyncio.run(run())

    assert len(result.references) == 1
    assert result.used_tokens_by_source["semantic_memory"] > len(result.references[0].summary) // 4
    assert result.used_tokens_by_source["semantic_memory"] <= 1_000
    rendered = _render_durable_context(
        build_deerflow_durable_context(
            _runtime(tmp_path / "projection"),
            memory_refs=result.references,
        )
    )
    assert estimated_tokens(rendered) <= result.used_tokens_by_source["semantic_memory"]
