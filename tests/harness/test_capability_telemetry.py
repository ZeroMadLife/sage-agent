"""Capability invocation telemetry contract tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolCallRequest
from sage_harness import CapabilityTelemetryMiddleware


@tool
def sample_tool(value: str) -> str:
    """Return one sample value."""
    return value


def _request(events: list[dict[str, object]]) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={
            "name": "sample_tool",
            "args": {"value": "secret-input"},
            "id": "call-1",
            "type": "tool_call",
        },
        tool=sample_tool,
        state={},
        runtime=SimpleNamespace(stream_writer=events.append),  # type: ignore[arg-type]
    )


def test_telemetry_emits_content_free_success_and_failure() -> None:
    events: list[dict[str, object]] = []
    middleware = CapabilityTelemetryMiddleware(
        {"sample_tool": "local:sample_tool"},
        "catalog-r1",
    )

    success = middleware.wrap_tool_call(
        _request(events),
        lambda request: ToolMessage(
            content="private result",
            tool_call_id=str(request.tool_call["id"]),
            name="sample_tool",
        ),
    )
    failure = middleware.wrap_tool_call(
        _request(events),
        lambda request: ToolMessage(
            content="private failure /Users/example token=secret",
            tool_call_id=str(request.tool_call["id"]),
            name="sample_tool",
            status="error",
        ),
    )

    assert isinstance(success, ToolMessage)
    assert isinstance(failure, ToolMessage)
    assert [event["status"] for event in events] == ["success", "failure"]
    assert events[0]["capability_id"] == "local:sample_tool"
    assert events[1]["failure_category"] == "tool_error"
    assert "secret-input" not in str(events)
    assert "private" not in str(events)
    assert "/Users/" not in str(events)


@pytest.mark.asyncio
async def test_telemetry_classifies_timeout_without_leaking_exception() -> None:
    events: list[dict[str, object]] = []
    middleware = CapabilityTelemetryMiddleware(
        {"sample_tool": "local:sample_tool"},
        "catalog-r1",
    )

    async def fail(_: ToolCallRequest) -> ToolMessage:
        raise TimeoutError("api_key=must-not-leak")

    with pytest.raises(TimeoutError):
        await middleware.awrap_tool_call(_request(events), fail)

    assert events[0]["status"] == "failure"
    assert events[0]["failure_category"] == "timeout"
    assert "api_key" not in str(events)


def test_telemetry_ignores_unbound_internal_tools() -> None:
    events: list[dict[str, object]] = []
    middleware = CapabilityTelemetryMiddleware({}, "catalog-r1")

    middleware.wrap_tool_call(
        _request(events),
        lambda request: ToolMessage(
            content="ok",
            tool_call_id=str(request.tool_call["id"]),
            name="sample_tool",
        ),
    )

    assert events == []
