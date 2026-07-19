"""Sage application adapter tests for awaited read-only children."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sage_harness import (
    EvidenceBundle,
    EvidenceBundleItem,
    KnowledgeEvidence,
    KnowledgeRetrievalResult,
    SubagentRequest,
    WebEvidence,
    WebFetchedDocument,
    WebFetchResult,
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


class CountingWebSearchPort(FakeWebSearchPort):
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: str, **kwargs) -> WebSearchResult:  # type: ignore[no-untyped-def]
        self.calls += 1
        return await super().search(query, **kwargs)


class CountingWebFetchPort:
    available = True

    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, url: str) -> WebFetchResult:
        self.calls += 1
        return WebFetchResult(
            status="evidence_found",
            document=WebFetchedDocument(
                canonical_url=url,
                title="Fetched research",
                text="Full fetched public evidence.",
                media_type="text/html",
                retrieved_at="2026-07-19T00:00:00Z",
                content_hash="b" * 64,
                wire_bytes=100,
            ),
        )


class UnavailableKnowledgePort(FakeKnowledgePort):
    available = False


class FakeEvidenceBundlePort:
    available = True

    async def read(self, thread_id: str, parent_run_id: str, **kwargs) -> EvidenceBundle:  # type: ignore[no-untyped-def]
        assert thread_id == "session-parent"
        assert parent_run_id == "run-parent"
        return EvidenceBundle(
            status="evidence_found",
            items=(
                EvidenceBundleItem(
                    evidence_ref="kcite_research",
                    kind="knowledge",
                    title="Approved evidence",
                    content="Approved project evidence.",
                    source_ref="source_approved",
                    token_count=6,
                ),
            ),
            requested_refs=tuple(kwargs["evidence_refs"]),
            token_budget=int(kwargs["token_budget"]),
            used_tokens=6,
        )


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


def test_synthesize_profile_is_read_only_and_requires_evidence_bundle_port() -> None:
    enabled = build_coding_subagent_config(
        FakeKnowledgePort(),
        FakeWebSearchPort(),
        None,
        evidence_bundle_port=FakeEvidenceBundlePort(),
    )
    disabled = build_coding_subagent_config(
        FakeKnowledgePort(),
        FakeWebSearchPort(),
        None,
    )

    synthesize = enabled.resolve("synthesize")
    assert synthesize is not None
    assert synthesize.tool_scope == ("read_evidence_bundle",)
    assert synthesize.token_budget == 16_000
    assert disabled.resolve("synthesize") is None


def test_practice_profile_is_server_owned_and_available_without_research_ports() -> None:
    config = build_coding_subagent_config(
        UnavailableKnowledgePort(),
        None,
        None,
    )

    practice = config.resolve("practice")

    assert practice is not None
    assert practice.tool_scope == (
        "list_files",
        "read_file",
        "search",
        "write_file",
        "patch_file",
        "run_shell",
    )
    assert practice.token_budget == 24_000
    assert practice.timeout_seconds == 300
    assert practice.max_steps == 20


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


def _practice_request(tmp_path: Path, child_run_id: str) -> SubagentRequest:
    return replace(
        _request(tmp_path, child_run_id),
        description="run one deterministic practice test",
        prompt="Run the existing deterministic test and report the observed result.",
        subagent_type="practice",
        tool_scope=(
            "list_files",
            "read_file",
            "search",
            "write_file",
            "patch_file",
            "run_shell",
        ),
        token_budget=24_000,
        timeout_seconds=30,
        max_steps=20,
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
    assert first.token_usage == request.token_budget
    assert first.model_calls == 2
    assert first.tool_count == 1
    child_run = runtime.run_store.get_run("child_test")
    assert child_run["events"][0]["type"] == "subagent_started"
    assert child_run["events"][-2]["type"] == "subagent_terminal"
    assert child_run["events"][-2]["status"] == "succeeded"
    assert child_run["events"][-1] == {
        "run_id": "child_test",
        "status": "completed",
        "type": "run_finished",
    }


def test_practice_subagent_records_passing_test_as_candidate_and_replays_it(
    tmp_path: Path,
) -> None:
    (tmp_path / "test_practice.py").write_text(
        "import unittest\n\n"
        "class PracticeTest(unittest.TestCase):\n"
        "    def test_value(self):\n"
        "        self.assertEqual(2 + 2, 4)\n",
        encoding="utf-8",
    )
    model = FakeModel(
        [
            '<tool>{"name":"run_shell","args":{"command":'
            '"python3 -m unittest -q test_practice.py"}}</tool>',
            "<final>The deterministic test passed.</final>",
        ]
    )
    runtime = _runtime(tmp_path, model)
    runtime.set_permission_mode("auto")
    executor = CodingSubagentExecutor(runtime)
    request = _practice_request(tmp_path, "child_practice_pass")

    first = asyncio.run(executor.execute(request))
    replayed = asyncio.run(executor.execute(request))

    assert first.status == "succeeded"
    assert replayed == first
    assert len(first.mastery_evidence) == 1
    candidate = first.mastery_evidence[0]
    assert candidate.kind == "code_test"
    assert candidate.result == "pass"
    assert candidate.metadata["exit_code"] == 0
    assert "command" not in candidate.metadata
    terminal = runtime.run_store.get_run(request.child_run_id)["events"][-2]
    assert terminal["mastery_evidence_count"] == 1
    assert terminal["mastery_evidence"][0]["result"] == "pass"


def test_practice_subagent_records_failing_test_without_claiming_mastery(
    tmp_path: Path,
) -> None:
    (tmp_path / "test_practice.py").write_text(
        "import unittest\n\n"
        "class PracticeTest(unittest.TestCase):\n"
        "    def test_value(self):\n"
        "        self.assertEqual(2 + 2, 5)\n",
        encoding="utf-8",
    )
    model = FakeModel(
        [
            '<tool>{"name":"run_shell","args":{"command":'
            '"python3 -m unittest -q test_practice.py"}}</tool>',
            "<final>The deterministic test failed; no mastery is claimed.</final>",
        ]
    )
    runtime = _runtime(tmp_path, model)
    runtime.set_permission_mode("auto")
    result = asyncio.run(
        CodingSubagentExecutor(runtime).execute(
            _practice_request(tmp_path, "child_practice_fail")
        )
    )

    assert result.status == "succeeded"
    assert len(result.mastery_evidence) == 1
    assert result.mastery_evidence[0].result == "fail"
    assert result.mastery_evidence[0].metadata["exit_code"] != 0


def test_practice_subagent_rejects_shell_commands_that_mask_test_exit_code(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        [
            '<tool>{"name":"run_shell","args":{"command":'
            '"python3 -m unittest -q missing_test.py || true"}}</tool>',
            "<final>The command completed, but it is not valid mastery evidence.</final>",
        ]
    )
    runtime = _runtime(tmp_path, model)
    runtime.set_permission_mode("auto")

    result = asyncio.run(
        CodingSubagentExecutor(runtime).execute(
            _practice_request(tmp_path, "child_practice_masked")
        )
    )

    assert result.status == "succeeded"
    assert result.mastery_evidence == ()


def test_practice_subagent_rejects_background_test_commands(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '<tool>{"name":"run_shell","args":{"command":'
            '"python3 -m unittest -q test_practice.py & true"}}</tool>',
            "<final>No deterministic receipt was recorded.</final>",
        ]
    )
    runtime = _runtime(tmp_path, model)
    runtime.set_permission_mode("auto")

    result = asyncio.run(
        CodingSubagentExecutor(runtime).execute(
            _practice_request(tmp_path, "child_practice_background")
        )
    )

    assert result.mastery_evidence == ()


def test_practice_subagent_reuses_parent_plan_mode_and_cannot_write(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '<tool>{"name":"write_file","args":'
            '{"path":"blocked.txt","content":"must not exist"}}</tool>',
            "<final>The parent policy blocked the write.</final>",
        ]
    )
    runtime = _runtime(tmp_path, model)
    runtime.set_permission_mode("plan")
    request = _practice_request(tmp_path, "child_practice_plan")

    result = asyncio.run(CodingSubagentExecutor(runtime).execute(request))

    assert result.status == "succeeded"
    assert result.mastery_evidence == ()
    assert not (tmp_path / "blocked.txt").exists()
    tool_result = next(
        event
        for event in runtime.run_store.get_run(request.child_run_id)["events"]
        if event["type"] == "tool_result"
    )
    assert tool_result["is_error"] is True
    assert tool_result["content"] == "plan_mode_tool_not_allowed"


def test_practice_subagent_surfaces_parent_approval_and_continues_in_place(
    tmp_path: Path,
) -> None:
    model = FakeModel(
        [
            '<tool>{"name":"write_file","args":'
            '{"path":"approved.txt","content":"approved"}}</tool>',
            "<final>The approved edit completed.</final>",
        ]
    )
    runtime = _runtime(tmp_path, model)
    request = _practice_request(tmp_path, "child_practice_approval")
    progress_events: list[dict[str, object]] = []

    async def run() -> object:
        execution = asyncio.create_task(
            CodingSubagentExecutor(runtime).execute(request, progress_events.append)
        )
        for _ in range(100):
            pending = runtime.approval_manager.pending(runtime.session_id)
            if pending is not None:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("practice approval was not surfaced")
        assert runtime.approval_manager.resolve(
            runtime.session_id,
            str(pending["approval_id"]),
            "once",
        )
        return await asyncio.wait_for(execution, timeout=2)

    result = asyncio.run(run())

    assert result.status == "succeeded"  # type: ignore[union-attr]
    assert (tmp_path / "approved.txt").read_text(encoding="utf-8") == "approved"
    approval = next(
        event for event in progress_events if event.get("phase") == "approval_required"
    )
    assert approval["status"] == "waiting"
    assert approval["tool"] == "write_file"


def test_coding_subagent_fails_closed_for_legacy_cached_usage(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, FakeModel(["<final>unused</final>"]))
    executor = CodingSubagentExecutor(runtime)
    request = _request(tmp_path, "child_legacy")
    runtime.run_store.start_run(request.child_run_id)
    runtime.run_store.append_trace(
        request.child_run_id,
        {
            "type": "subagent_terminal",
            "status": "succeeded",
            "result_brief": "legacy result",
            "result_ref": "subagent://session-parent/child_legacy",
        },
    )

    result = asyncio.run(executor.execute(request))

    assert result.token_usage == request.token_budget
    assert result.model_calls == request.max_steps + 2
    assert result.tool_count == request.max_steps


def test_coding_subagent_rejects_write_capability_before_execution(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, FakeModel(["<final>unused</final>"]))
    executor = CodingSubagentExecutor(runtime)
    request = replace(
        _request(tmp_path),
        tool_scope=("read_file", "write_file"),
    )

    with pytest.raises(ValueError, match="server-owned scope"):
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
    assert len(result.query_fingerprints) == 2
    assert all(item.startswith("query_") for item in result.query_fingerprints)
    assert len(result.source_fingerprints) == 2
    assert result.model_calls == 3
    assert result.tool_count == 2
    assert result.token_usage == request.token_budget
    assert [event["phase"] for event in progress].count("tool_completed") == 2
    assert progress[-1]["evidence_count"] == 2
    trace = runtime.run_store.get_run("child_research")["events"]
    terminal = next(event for event in reversed(trace) if event["type"] == "subagent_terminal")
    assert terminal["evidence_refs"] == ["kcite_research", "wcite_research"]
    assert terminal["query_fingerprints"] == list(result.query_fingerprints)


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


def test_research_subagent_breaks_duplicate_queries_from_parent_state(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '<tool>{"name":"search_web","args":{"query":"Harness evidence"}}</tool>',
            '<tool>{"name":"search_web","args":{"query":"Harness evidence"}}</tool>',
            "<final>First pass [wcite_research].</final>",
            '<tool>{"name":"search_web","args":{"query":"Harness evidence"}}</tool>',
            "<final>The duplicate query was skipped.</final>",
            '<tool>{"name":"search_web","args":{"query":"Harness evidence"}}</tool>',
            "<final>A later run refreshed [wcite_research].</final>",
        ]
    )
    port = CountingWebSearchPort()
    runtime = _runtime(tmp_path, model)
    executor = CodingSubagentExecutor(
        runtime,
        knowledge_port=FakeKnowledgePort(),
        web_search_port=port,
    )
    first_request = replace(
        _request(tmp_path, "child_first_query"),
        subagent_type="research",
        tool_scope=("knowledge_search", "search_web"),
    )
    first = asyncio.run(executor.execute(first_request))
    second_request = replace(
        first_request,
        child_run_id="child_duplicate_query",
        query_fingerprints=first.query_fingerprints,
    )

    second = asyncio.run(executor.execute(second_request))

    runtime.active_run_id = "run-next"
    third_request = replace(
        first_request,
        parent_run_id="run-next",
        child_run_id="child_refresh_query",
        query_fingerprints=first.query_fingerprints,
    )
    third = asyncio.run(executor.execute(third_request))

    assert port.calls == 2
    assert second.status == "succeeded"
    assert second.evidence_refs == ()
    assert third.evidence_refs == ("wcite_research",)


def test_research_subagent_allows_first_fetch_after_search_then_breaks_repeat(
    tmp_path: Path,
) -> None:
    url = "https://example.com/research"
    model = FakeModel(
        [
            '<tool>{"name":"search_web","args":{"query":"Harness evidence"}}</tool>',
            f'<tool>{{"name":"fetch_web","args":{{"url":"{url}"}}}}</tool>',
            f'<tool>{{"name":"fetch_web","args":{{"url":"{url}"}}}}</tool>',
            "<final>The fetched citation is current.</final>",
        ]
    )
    fetch_port = CountingWebFetchPort()
    runtime = _runtime(tmp_path, model)
    executor = CodingSubagentExecutor(
        runtime,
        knowledge_port=FakeKnowledgePort(),
        web_search_port=FakeWebSearchPort(),
        web_fetch_port=fetch_port,
    )
    request = replace(
        _request(tmp_path, "child_fetch_breaker"),
        subagent_type="research",
        tool_scope=("knowledge_search", "search_web", "fetch_web"),
    )

    result = asyncio.run(executor.execute(request))

    assert result.status == "succeeded"
    assert fetch_port.calls == 1
    assert "wcite_research" in result.evidence_refs
    assert any(reference.startswith("sage://coding/") for reference in result.evidence_refs)


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


def test_synthesize_subagent_can_only_read_parent_research_bundle(tmp_path: Path) -> None:
    model = FakeModel(
        [
            '<tool>{"name":"read_evidence_bundle","args":{"token_budget":512}}</tool>',
            "<final>Approved [kcite_research] supports the conclusion.</final>",
        ]
    )
    runtime = _runtime(tmp_path, model)
    executor = CodingSubagentExecutor(
        runtime,
        evidence_bundle_port=FakeEvidenceBundlePort(),
    )
    request = replace(
        _request(tmp_path, "child_synthesis"),
        description="synthesize research",
        prompt="Summarize only the approved evidence.",
        subagent_type="synthesize",
        tool_scope=("read_evidence_bundle",),
        evidence_refs=("kcite_research",),
        evidence_child_run_ids=("child_research",),
    )

    result = asyncio.run(executor.execute(request))

    assert result.status == "succeeded"
    assert result.evidence_refs == ("kcite_research",)
    assert result.tool_count == 1
    trace = runtime.run_store.get_run("child_synthesis")["events"]
    assert trace[-2]["status"] == "succeeded"
    assert trace[-2]["evidence_refs"] == ["kcite_research"]


def test_synthesize_subagent_fails_if_it_skips_the_bundle(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, FakeModel(["<final>unsupported claim</final>"]))
    executor = CodingSubagentExecutor(runtime, evidence_bundle_port=FakeEvidenceBundlePort())
    request = replace(
        _request(tmp_path, "child_synthesis_no_read"),
        subagent_type="synthesize",
        tool_scope=("read_evidence_bundle",),
        evidence_refs=("kcite_research",),
        evidence_child_run_ids=("child_research",),
    )

    result = asyncio.run(executor.execute(request))

    assert result.status == "failed"
    assert result.error_code == "evidence_bundle_not_read"
