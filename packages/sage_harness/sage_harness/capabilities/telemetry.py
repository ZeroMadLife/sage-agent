"""Content-free capability invocation telemetry for host-side aggregation."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, override

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from sage_harness.config import HarnessRunContext
from sage_harness.state import SageThreadState

CapabilityFailureCategory = Literal[
    "approval_denied",
    "execution_error",
    "policy_blocked",
    "timeout",
    "tool_error",
    "unavailable",
]


class CapabilityTelemetryMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Emit bounded invocation outcomes without arguments, content, or exceptions."""

    state_schema = SageThreadState

    def __init__(
        self,
        capability_ids_by_tool_name: Mapping[str, str],
        catalog_revision: str,
    ) -> None:
        super().__init__()
        if not catalog_revision.strip():
            raise ValueError("capability telemetry requires a catalog revision")
        self._capability_ids = {
            str(name): str(capability_id)
            for name, capability_id in capability_ids_by_tool_name.items()
            if str(name).strip() and str(capability_id).strip()
        }
        self._catalog_revision = catalog_revision

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        capability_id = self._capability_id(request)
        if capability_id is None:
            return handler(request)
        started_at = time.perf_counter()
        try:
            result = handler(request)
        except TimeoutError:
            self._emit(request, capability_id, started_at, "failure", "timeout")
            raise
        except Exception:
            self._emit(request, capability_id, started_at, "failure", "execution_error")
            raise
        failure_category = _result_failure_category(result)
        status: Literal["success", "failure"] = (
            "failure" if failure_category is not None else "success"
        )
        self._emit(
            request,
            capability_id,
            started_at,
            status,
            failure_category,
        )
        return result

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        capability_id = self._capability_id(request)
        if capability_id is None:
            return await handler(request)
        started_at = time.perf_counter()
        try:
            result = await handler(request)
        except TimeoutError:
            self._emit(request, capability_id, started_at, "failure", "timeout")
            raise
        except Exception:
            self._emit(request, capability_id, started_at, "failure", "execution_error")
            raise
        failure_category = _result_failure_category(result)
        status: Literal["success", "failure"] = (
            "failure" if failure_category is not None else "success"
        )
        self._emit(
            request,
            capability_id,
            started_at,
            status,
            failure_category,
        )
        return result

    def _capability_id(self, request: ToolCallRequest) -> str | None:
        name = str(request.tool_call.get("name") or "")
        return self._capability_ids.get(name)

    def _emit(
        self,
        request: ToolCallRequest,
        capability_id: str,
        started_at: float,
        status: Literal["success", "failure"],
        failure_category: CapabilityFailureCategory | None,
    ) -> None:
        writer = getattr(request.runtime, "stream_writer", None)
        if not callable(writer):
            return
        payload: dict[str, object] = {
            "type": "capability_invocation_completed",
            "version": 1,
            "capability_id": capability_id,
            "catalog_revision": self._catalog_revision,
            "status": status,
            "duration_ms": max(0, round((time.perf_counter() - started_at) * 1000)),
        }
        if failure_category is not None:
            payload["failure_category"] = failure_category
        writer(payload)


def _result_failure_category(
    result: ToolMessage | Command[Any],
) -> CapabilityFailureCategory | None:
    messages: list[ToolMessage] = []
    if isinstance(result, ToolMessage):
        messages.append(result)
    else:
        update = result.update
        if isinstance(update, Mapping):
            raw_messages = update.get("messages", ())
            if isinstance(raw_messages, ToolMessage):
                messages.append(raw_messages)
            elif isinstance(raw_messages, list | tuple):
                messages.extend(
                    message
                    for message in raw_messages
                    if isinstance(message, ToolMessage)
                )
    failed = next((message for message in messages if message.status == "error"), None)
    if failed is None:
        return None
    content = str(failed.content).casefold()
    if "approval denied" in content:
        return "approval_denied"
    if "timed out" in content or "timeout" in content:
        return "timeout"
    if "not been promoted" in content or "not allowed" in content or "policy" in content:
        return "policy_blocked"
    if "unavailable" in content or "unknown tool" in content:
        return "unavailable"
    return "tool_error"


__all__ = ["CapabilityFailureCategory", "CapabilityTelemetryMiddleware"]
