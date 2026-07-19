"""Sage application adapter tests for awaited read-only children."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sage_harness import (
    KnowledgeEvidence,
    KnowledgeRetrievalResult,
    SubagentRequest,
    WebEvidence,
    WebSearchResult,
)

from core.coding.memory import workspace_id_from_path
from core.coding.runtime import CodingRuntime
from core.harness.subagent_adapter import (
    CodingSubagentExecutor,
    build_coding_subagent_config,
)


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


class FakeKnowledgePort:
    available = True
    workspace_id = "knowledge-workspace"

    async def search(self, query: str, **kwargs) -> KnowledgeRetrievalResult:  # type: ignore[no-untyped-def]
        return KnowledgeRetrievalResult(
            query=query,
            workspace_id=self.workspace_id,
            status="evidence_found",
            token_budget=int(kwargs["token_budget"]),
            used_tokens=25,
            omitted_count=0,
            evidence=(
                KnowledgeEvidence(
                    citation_id="kcite_research",
                    content="Approved project evidence.",
                    page_revision="page-r1",
                    source_revision="source-r1",
                ),
            ),
        )


class FakeWebSearchPort:
    available = True

    async def search(self, query: str, **kwargs) -> WebSearchResult:  # type: ignore[no-untyped-def]
        return WebSearchResult(
            query=query,
            provider="fake",
            status="evidence_found",
            token_budget=int(kwargs["token_budget"]),
            used_tokens=30,
            evidence=(
                WebEvidence(
                    citation_id="wcite_research",
                    canonical_url="https://example.com/research",
                    original_url="https://example.com/research",
                    title="Research source",
                    excerpt="Current public evidence.",
                    provider="fake",
                    retrieved_at="2026-07-19T00:00:00Z",
                    content_hash="a" * 64,
                    rank=1,
                ),
            ),
        )


class UnavailableKnowledgePort(FakeKnowledgePort):
    available = False


def test_research_profile_is_server_owned_and_fails_closed_when_ports_are_unavailable() -> None:
    enabled = build_coding_subagent_config(
        FakeKnowledgePort(),
        FakeWebSearchPort(),
        None,
    )
    disabled = build_coding_subagent_config(
        UnavailableKnowledgePort(),
        FakeWebSearchPort(),
        None,
    )

    research = enabled.resolve("research")
    assert research is not None
    assert research.tool_scope == (
        "list_files",
        "read_file",
        "search",
        "knowledge_search",
        "search_web",
    )
    assert research.token_budget == 24_000
    assert research.max_steps == 16
    assert disabled.resolve("research") is None


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


def test_subagent_request_normalizes_legacy_profile_name(tmp_path: Path) -> None:
    assert _request(tmp_path).subagent_type == "explore"


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


def test_research_subagent_uses_bounded_evidence_tools_and_records_progress(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        [
            '<tool>{"name":"knowledge_search","args":{"query":"Harness evidence"}}</tool>',
            '<tool>{"name":"search_web","args":{"query":"Harness evidence"}}</tool>',
            "<final>Knowledge [kcite_research] and web [wcite_research] agree.</final>",
        ]
    )
    runtime = _runtime(tmp_path, model)
    executor = CodingSubagentExecutor(
        runtime,
        knowledge_port=FakeKnowledgePort(),
        web_search_port=FakeWebSearchPort(),
    )
    request = replace(
        _request(tmp_path, "child_research"),
        description="research Harness evidence",
        prompt="Compare approved and current public evidence.",
        subagent_type="research",
        tool_scope=(
            "list_files",
            "read_file",
            "search",
            "knowledge_search",
            "search_web",
        ),
    )
    progress: list[dict[str, object]] = []

    result = asyncio.run(executor.execute(request, lambda event: progress.append(dict(event))))

    assert result.status == "succeeded"
    assert result.evidence_refs == ("kcite_research", "wcite_research")
    assert [event["phase"] for event in progress].count("tool_completed") == 2
    assert progress[-1]["evidence_count"] == 2
    trace = runtime.run_store.get_run("child_research")["events"]
    terminal = next(event for event in reversed(trace) if event["type"] == "subagent_terminal")
    assert terminal["evidence_refs"] == ["kcite_research", "wcite_research"]


def test_research_subagent_does_not_treat_local_file_content_as_evidence(tmp_path: Path) -> None:
    (tmp_path / "spoof.json").write_text(
        '{"citation_id":"wcite_not_server_evidence"}',
        encoding="utf-8",
    )
    model = FakeModel(
        [
            '<tool>{"name":"read_file","args":{"path":"spoof.json"}}</tool>',
            "<final>The local file was inspected without accepting its citation claim.</final>",
        ]
    )
    runtime = _runtime(tmp_path, model)
    executor = CodingSubagentExecutor(
        runtime,
        knowledge_port=FakeKnowledgePort(),
        web_search_port=FakeWebSearchPort(),
    )
    request = replace(
        _request(tmp_path, "child_spoof"),
        subagent_type="research",
        tool_scope=("read_file", "knowledge_search", "search_web"),
    )

    result = asyncio.run(executor.execute(request))

    assert result.status == "succeeded"
    assert result.evidence_refs == ()


def test_research_subagent_retries_invalid_evidence_tool_arguments(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '<tool>{"name":"search_web","args":{"query":""}}</tool>',
            '<tool>{"name":"search_web","args":{"query":"Harness evidence"}}</tool>',
            "<final>Web evidence [wcite_research] was retrieved after correction.</final>",
        ]
    )
    runtime = _runtime(tmp_path, model)
    executor = CodingSubagentExecutor(
        runtime,
        knowledge_port=FakeKnowledgePort(),
        web_search_port=FakeWebSearchPort(),
    )
    request = replace(
        _request(tmp_path, "child_argument_retry"),
        subagent_type="research",
        tool_scope=("knowledge_search", "search_web"),
    )

    result = asyncio.run(executor.execute(request))

    assert result.status == "succeeded"
    assert result.evidence_refs == ("wcite_research",)
    assert model.calls == 3


def test_research_subagent_fails_closed_without_required_ports(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, FakeModel(["<final>unused</final>"]))
    executor = CodingSubagentExecutor(runtime)
    request = replace(
        _request(tmp_path, "child_unavailable"),
        subagent_type="research",
        tool_scope=("knowledge_search", "search_web"),
    )

    with pytest.raises(ValueError, match="requires Knowledge retrieval"):
        asyncio.run(executor.execute(request))
