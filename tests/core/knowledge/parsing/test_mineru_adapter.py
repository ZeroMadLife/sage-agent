"""MinerU signed-upload and polling protocol tests."""

from __future__ import annotations

import httpx
import pytest

from core.knowledge.parsing import (
    ExternalAdapterError,
    ExternalParsePending,
    ExternalParseProgress,
    ParseRequest,
)
from core.knowledge.parsing.adapters import MinerUAdapter, MinerUConfig


def _request() -> ParseRequest:
    return ParseRequest(
        source_id="src_scan",
        relative_path="reports/scan.pdf",
        source_revision="sha256:scan",
        media_type="application/pdf",
        payload=b"%PDF-scanned",
    )


async def test_mineru_uploads_polls_and_downloads_bounded_markdown() -> None:
    poll_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal poll_count
        if request.method == "POST" and request.url.path.endswith("/parse/file"):
            assert b'"is_ocr":true' in request.content
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "task_id": "task-1",
                        "file_url": "https://oss-mineru.openxlab.org.cn/upload/scan.pdf?sig=1",
                    },
                },
            )
        if request.method == "PUT":
            assert request.content == b"%PDF-scanned"
            return httpx.Response(200)
        if request.url.path.endswith("/parse/task-1"):
            poll_count += 1
            if poll_count == 1:
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"task_id": "task-1", "state": "running"}},
                )
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "task_id": "task-1",
                        "state": "done",
                        "markdown_url": ("https://cdn-mineru.openxlab.org.cn/pdf/task-1/full.md"),
                    },
                },
            )
        if request.url.path.endswith("/full.md"):
            return httpx.Response(200, content=b"# OCR Report\n\nRecovered table.\n")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = MinerUAdapter(MinerUConfig(poll_seconds=0.001), client=client)
    events: list[ExternalParseProgress] = []

    async def report(event: ExternalParseProgress) -> None:
        events.append(event)

    document = await adapter.parse(_request(), progress=report)

    assert document.title == "scan"
    assert document.provenance.parser_id == "mineru.agent"
    assert "Recovered table" in document.rendered_markdown
    assert [event.stage for event in events] == ["uploading", "queued", "running"]
    await client.aclose()


async def test_mineru_submit_and_resume_each_perform_one_bounded_remote_step() -> None:
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.method == "POST" and request.url.path.endswith("/parse/file"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "task_id": "task-one-step",
                        "file_url": "https://oss-mineru.openxlab.org.cn/upload/scan.pdf?sig=1",
                    },
                },
            )
        if request.method == "PUT":
            return httpx.Response(200)
        if request.url.path.endswith("/parse/task-one-step"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"task_id": "task-one-step", "state": "running"},
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = MinerUAdapter(MinerUConfig(poll_seconds=2), client=client)

    submitted = await adapter.submit(_request(), progress=_ignore)
    assert requests == [
        ("POST", "/api/v1/agent/parse/file"),
        ("PUT", "/upload/scan.pdf"),
    ]

    resumed = await adapter.resume(_request(), submitted.ticket, progress=_ignore)
    assert isinstance(resumed, ExternalParsePending)
    assert resumed.stage == "running"
    assert requests == [
        ("POST", "/api/v1/agent/parse/file"),
        ("PUT", "/upload/scan.pdf"),
        ("GET", "/api/v1/agent/parse/task-one-step"),
    ]
    await client.aclose()


async def test_mineru_rejects_untrusted_signed_upload_url() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "task_id": "task-1",
                    "file_url": "http://127.0.0.1/private",
                },
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = MinerUAdapter(MinerUConfig(), client=client)

    with pytest.raises(ExternalAdapterError) as captured:
        await adapter.parse(_request(), progress=_ignore)

    assert captured.value.code == "unsafe_url"
    assert captured.value.retryable is False
    await client.aclose()


async def _ignore(_: ExternalParseProgress) -> None:
    return None
