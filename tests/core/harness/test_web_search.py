"""Provider-neutral Web Search and current-turn evidence contracts."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
from langchain_core.messages import ToolMessage
from sage_harness.runtime.events import HarnessStreamItem

from core.coding.runtime import CodingRuntime
from core.harness.event_adapter import HarnessEventAdapter
from core.harness.tools_adapter import build_deerflow_coding_tool_bundle
from core.harness.web_search import SearxngWebSearchAdapter, build_web_search_tool


def _adapter(payload: object, *, status: int = 200) -> tuple[SearxngWebSearchAdapter, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload)

    return (
        SearxngWebSearchAdapter(
            "https://search.example.test",
            client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                follow_redirects=False,
                trust_env=False,
            ),
            clock=lambda: datetime(2026, 7, 18, 8, 30, tzinfo=UTC),
        ),
        seen,
    )


def test_searxng_adapter_returns_bounded_https_evidence_and_filters_domains() -> None:
    adapter, seen = _adapter(
        {
            "results": [
                {
                    "url": "https://docs.example.com/page#section",
                    "title": "<b>Harness</b> docs",
                    "content": "<em>Trusted claim</em> " + "x" * 2_000,
                    "engine": "documentation",
                },
                {
                    "url": "http://docs.example.com/insecure",
                    "title": "HTTP",
                    "content": "must be rejected",
                },
                {
                    "url": "https://other.example.net/page",
                    "title": "Other",
                    "content": "outside domain filter",
                },
            ]
        }
    )

    result = asyncio.run(
        adapter.search(
            "agent harness",
            top_k=5,
            freshness="month",
            domains=("example.com",),
            language="zh-CN",
        )
    )

    assert result.status == "evidence_found"
    assert len(result.evidence) == 1
    evidence = result.evidence[0]
    assert evidence.canonical_url == "https://docs.example.com/page"
    assert evidence.title == "Harness docs"
    assert len(evidence.excerpt) == 1_500
    assert evidence.citation_id.startswith("wcite_")
    assert evidence.content_hash and evidence.retrieved_at == "2026-07-18T08:30:00Z"
    assert seen[0].url.path == "/search"
    assert seen[0].url.params["format"] == "json"
    assert seen[0].url.params["time_range"] == "month"
    assert "example.com" not in seen[0].url.params["q"]


def test_searxng_adapter_fails_closed_without_leaking_provider_error() -> None:
    adapter, _ = _adapter({"secret": "do-not-leak"}, status=503)

    result = asyncio.run(adapter.search("current information"))

    assert result.status == "unavailable"
    assert result.error_code == "provider_unavailable"
    assert "secret" not in repr(result)


def test_searxng_adapter_reports_invalid_provider_json() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    adapter = SearxngWebSearchAdapter(
        "https://search.example.test",
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ),
    )

    result = asyncio.run(adapter.search("current information"))

    assert result.status == "unavailable"
    assert result.error_code == "invalid_provider_response"


def test_search_web_tool_and_timeline_keep_only_bounded_public_citations() -> None:
    adapter, _ = _adapter(
        {
            "results": [
                {
                    "url": f"https://example.com/{index}",
                    "title": f"Result {index}",
                    "content": "remote evidence " * 200,
                }
                for index in range(10)
            ]
        }
    )
    tool = build_web_search_tool(adapter)

    content = asyncio.run(tool.ainvoke({"query": "Sage Harness", "top_k": 8}))
    wrapped = f"<remote-content>\n{content}\n</remote-content>"
    events = HarnessEventAdapter(session_id="s1", run_id="r1").adapt(
        HarnessStreamItem(
            1,
            "messages",
            (
                ToolMessage(
                    content=wrapped,
                    tool_call_id="call-web",
                    name="search_web",
                ),
                {},
            ),
            "source-web",
        )
    )

    public_content = str(events[-1].payload["content"])
    public = json.loads(public_content)
    assert len(public_content) <= 4_000
    assert public["status"] == "evidence_found"
    assert public["remote_content"] is True
    assert public["citations"]
    assert public["citations"][0]["citation_id"].startswith("wcite_")
    assert "instruction" not in public
    assert "original_url" not in public_content


def test_search_web_enforces_a_per_call_evidence_token_budget() -> None:
    adapter, _ = _adapter(
        {
            "results": [
                {
                    "url": f"https://example.com/{index}",
                    "title": f"Result {index}",
                    "content": "网页证据" * 1_000,
                }
                for index in range(6)
            ]
        }
    )
    tool = build_web_search_tool(adapter)

    content = asyncio.run(
        tool.ainvoke({"query": "Sage Harness", "top_k": 6, "token_budget": 256})
    )
    payload = json.loads(content)

    assert payload["status"] == "evidence_found"
    assert payload["token_budget"] == 256
    assert 0 < payload["used_tokens"] <= 256
    assert payload["omitted_count"] >= 1
    assert payload["citations"]
    assert len(content) < 2_000


def test_search_web_counts_non_ascii_evidence_conservatively() -> None:
    adapter, _ = _adapter(
        {
            "results": [
                {
                    "url": "https://example.com/chinese",
                    "title": "中文资料",
                    "content": "证据" * 1_000,
                }
            ]
        }
    )

    result = asyncio.run(adapter.search("中文检索", token_budget=256))

    assert result.status == "evidence_found"
    assert 0 < result.used_tokens <= 256
    assert result.evidence[0].excerpt.endswith("...")
    assert len(result.evidence[0].excerpt) < 256


def test_search_web_is_discoverable_but_deferred_from_initial_model_tools(tmp_path) -> None:  # type: ignore[no-untyped-def]
    adapter, _ = _adapter({"results": []})
    runtime = CodingRuntime(
        session_id="s-web",
        workspace_root=tmp_path,
        model=object(),
        storage_root=tmp_path / ".coding",
        runtime_profile="deerflow_v2",
    )

    bundle = build_deerflow_coding_tool_bundle(
        runtime,
        run_id="r-web",
        web_search_port=adapter,
    )

    assert "search_web" in bundle.deferred_setup.deferred_names
    assert bundle.deferred_setup.selection_index is not None
    matches = bundle.deferred_setup.selection_index.discover("public web search")
    assert "web:search" in {
        match.descriptor.capability_id for match in matches
    }
    selected = bundle.deferred_setup.selection_index.select(("web:search",))
    assert [match.tool_name for match in selected.selected] == ["search_web"]
