"""Public HTML fetch safety, artifact, and capability contracts."""

from __future__ import annotations

import asyncio
import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import httpx
from langchain_core.messages import ToolMessage
from sage_harness.runtime.events import HarnessStreamItem

from core.coding.persistence.tool_result_store import ToolResultStore
from core.coding.runtime import CodingRuntime
from core.harness.event_adapter import HarnessEventAdapter
from core.harness.tools_adapter import build_deerflow_coding_tool_bundle
from core.harness.web_fetch import SafeWebFetchAdapter, build_web_fetch_tool


def _resolver_for(*addresses: str):  # type: ignore[no-untyped-def]
    def resolve(
        host: str,
        port: int,
        family: int,
        socktype: int,
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        del host, family
        return [(socket.AF_INET, socktype, 6, "", (address, port)) for address in addresses]

    return resolve


def _adapter(
    handler,  # type: ignore[no-untyped-def]
    *,
    resolver=None,  # type: ignore[no-untyped-def]
    max_wire_bytes: int = 2 * 1024 * 1024,
) -> SafeWebFetchAdapter:
    return SafeWebFetchAdapter(
        resolver=resolver or _resolver_for("93.184.216.34"),
        client_factory=lambda _: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        ),
        clock=lambda: datetime(2026, 7, 18, 9, 30, tzinfo=UTC),
        max_wire_bytes=max_wire_bytes,
    )


def test_fetch_normalizes_public_html_and_ignores_executable_content() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=(
                b"<html><head><title>Sage Docs</title><style>secret-style</style></head>"
                b"<body><h1>Harness</h1><script>ignore-me()</script><p>Public evidence</p>"
                b"</body></html>"
            ),
        )

    result = asyncio.run(_adapter(handler).fetch("https://example.com/docs#section"))

    assert result.status == "evidence_found"
    assert result.document is not None
    assert result.document.canonical_url == "https://example.com/docs"
    assert result.document.title == "Sage Docs"
    assert "Harness Public evidence" in result.document.text
    assert "ignore-me" not in result.document.text
    assert "secret-style" not in result.document.text
    assert result.document.retrieved_at == "2026-07-18T09:30:00Z"
    assert len(result.document.content_hash) == 64


def test_fetch_rejects_private_literal_and_mixed_dns_answers() -> None:
    async def unused(_: httpx.Request) -> httpx.Response:
        raise AssertionError("network must not be reached")

    literal = asyncio.run(_adapter(unused).fetch("https://127.0.0.1/private"))
    mixed = asyncio.run(
        _adapter(
            unused,
            resolver=_resolver_for("93.184.216.34", "10.0.0.2"),
        ).fetch("https://example.com/private")
    )

    assert literal.error_code == "invalid_url"
    assert mixed.error_code == "destination_not_allowed"


def test_fetch_revalidates_redirect_and_rejects_private_target() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://localhost/admin"})

    result = asyncio.run(_adapter(handler).fetch("https://example.com/start"))

    assert result.status == "unavailable"
    assert result.error_code == "destination_not_allowed"


def test_fetch_follows_bounded_public_redirects() -> None:
    seen: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/docs"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html><title>Docs</title><body>Final evidence</body></html>",
        )

    result = asyncio.run(_adapter(handler).fetch("https://example.com/start"))

    assert result.status == "evidence_found"
    assert result.document is not None
    assert result.document.canonical_url == "https://example.com/docs"
    assert [url.rsplit("/", 1)[-1] for url in seen] == ["start", "docs"]


def test_fetch_rejects_oversized_or_non_html_responses() -> None:
    async def too_large(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"x" * 65,
        )

    async def pdf(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=b"%PDF",
        )

    large = asyncio.run(_adapter(too_large, max_wire_bytes=64).fetch("https://example.com"))
    binary = asyncio.run(_adapter(pdf).fetch("https://example.com/report.pdf"))

    assert large.error_code == "response_too_large"
    assert binary.error_code == "unsupported_media_type"


def test_fetch_tool_archives_full_text_and_returns_bounded_timeline(tmp_path: Path) -> None:
    remote_text = "网页证据" * 4_000

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=f"<html><title>资料</title><body>{remote_text}</body></html>".encode(),
        )

    adapter = _adapter(handler)
    store = ToolResultStore(tmp_path / ".coding", "session-fetch", "run-fetch")
    tool = build_web_fetch_tool(adapter, store)
    content = asyncio.run(
        tool.ainvoke(
            {
                "name": "fetch_web",
                "args": {"url": "https://example.com/docs", "token_budget": 256},
                "id": "call-fetch",
                "type": "tool_call",
            }
        )
    )
    payload = json.loads(content.content)

    assert payload["status"] == "evidence_found"
    assert content.artifact["artifact_ref"] == payload["artifact_ref"]
    assert payload["used_tokens"] <= 256
    assert payload["excerpt"].endswith("...")
    assert store.read(payload["artifact_ref"]) == f"资料 {remote_text}"

    events = HarnessEventAdapter(session_id="session-fetch", run_id="run-fetch").adapt(
        HarnessStreamItem(
            1,
            "messages",
            (
                ToolMessage(
                    content=f"<remote-content>\n{content.content}\n</remote-content>",
                    tool_call_id="call-fetch",
                    name="fetch_web",
                ),
                {},
            ),
            "source-fetch",
        )
    )
    public_text = str(events[-1].payload["content"])
    public = json.loads(public_text)
    assert len(public_text) <= 4_000
    assert public["artifact_ref"].startswith("sage://coding/")
    assert public["remote_content"] is True
    assert "instruction" not in public
    assert remote_text not in public_text


def test_fetch_tool_max_budget_never_overwrites_full_artifact(tmp_path: Path) -> None:
    remote_text = "large public evidence " * 20_000

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=f"<html><title>Large</title><body>{remote_text}</body></html>".encode(),
        )

    store = ToolResultStore(tmp_path / ".coding", "session-large", "run-large")
    tool = build_web_fetch_tool(_adapter(handler), store)
    content = asyncio.run(
        tool.ainvoke(
            {
                "name": "fetch_web",
                "args": {"url": "https://example.com/large", "token_budget": 8_000},
                "id": "call-large",
                "type": "tool_call",
            }
        )
    )
    payload = json.loads(content.content)

    assert len(content.content) <= 10_000
    assert payload["used_tokens"] <= 8_000
    assert content.artifact["artifact_ref"] == payload["artifact_ref"]
    assert store.read(payload["artifact_ref"]) == f"Large {remote_text.strip()}"


def test_fetch_web_is_discoverable_but_deferred(tmp_path: Path) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"ok")

    runtime = CodingRuntime(
        session_id="session-fetch",
        workspace_root=tmp_path,
        model=object(),
        storage_root=tmp_path / ".coding",
        runtime_profile="deerflow_v2",
    )
    bundle = build_deerflow_coding_tool_bundle(
        runtime,
        run_id="run-fetch",
        web_fetch_port=_adapter(handler),
        artifact_store=ToolResultStore(tmp_path / ".coding", "session-fetch", "run-fetch"),
    )

    assert "fetch_web" in bundle.deferred_setup.deferred_names
    assert bundle.deferred_setup.selection_index is not None
    matches = bundle.deferred_setup.selection_index.discover("fetch public web page")
    assert "web:fetch" in {match.descriptor.capability_id for match in matches}
