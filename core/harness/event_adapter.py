"""Map public LangGraph stream items into Sage's durable run events."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from sage_harness.runtime.events import (
    HarnessStreamItem,
    bounded_state_summary,
    message_payload,
)

from core.coding.run_coordinator import RunEvent


class HarnessEventAdapter:
    """Translate graph events without persisting private model reasoning."""

    def __init__(
        self,
        *,
        session_id: str,
        run_id: str,
        stream_namespace: str = "initial",
        seen_tool_call_ids: Iterable[str] = (),
    ) -> None:
        if not session_id.strip() or not run_id.strip():
            raise ValueError("session_id and run_id are required")
        if not stream_namespace.strip():
            raise ValueError("stream_namespace must not be empty")
        self.session_id = session_id
        self.run_id = run_id
        self.stream_namespace = stream_namespace
        self._seen_model_tool_calls = {
            tool_call_id for tool_call_id in seen_tool_call_ids if tool_call_id
        }
        self._pending_model_calls_by_tool: dict[str, int] = {}
        self._custom_tool_results: set[str] = set()

    def adapt(self, item: HarnessStreamItem) -> tuple[RunEvent, ...]:
        """Return zero or more Sage events for one graph stream item."""
        if item.mode == "messages":
            return self._messages(item.payload, item.source_event_id)
        if item.mode == "updates":
            return self._updates(item.payload, item.source_event_id)
        if item.mode == "values":
            return self._values(item.payload, item.source_event_id)
        return self._custom(item.payload, item.source_event_id)

    def _messages(self, payload: Any, source_event_id: str) -> tuple[RunEvent, ...]:
        if not isinstance(payload, tuple) or not payload:
            return ()
        message = payload[0]
        metadata = payload[1] if len(payload) > 1 and isinstance(payload[1], Mapping) else {}
        projected = message_payload(message)
        message_type = str(projected.get("type", ""))
        content = projected.get("content", "")
        if message_type == "ai":
            tool_calls = projected.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                events: list[RunEvent] = []
                for index, call in enumerate(tool_calls):
                    if not isinstance(call, Mapping):
                        continue
                    name = str(call.get("name", ""))
                    tool_call_id = str(call.get("id", ""))
                    args = call.get("args", {})
                    if not name or not tool_call_id or tool_call_id in self._seen_model_tool_calls:
                        continue
                    self._seen_model_tool_calls.add(tool_call_id)
                    self._pending_model_calls_by_tool[name] = (
                        self._pending_model_calls_by_tool.get(name, 0) + 1
                    )
                    events.append(
                        self._event(
                            "tool",
                            "running",
                            {
                                "type": "tool_call",
                                "tool": name,
                                "args": args if isinstance(args, Mapping) else {},
                                "tool_call_id": tool_call_id,
                                "message_id": projected.get("id", ""),
                            },
                            source_event_id=f"{source_event_id}:call:{index}",
                        )
                    )
                return tuple(events)
            if content:
                return (
                    self._event(
                        "assistant",
                        "running",
                        {
                            "type": "text_delta",
                            "delta": content,
                            "message_id": projected.get("id", ""),
                            "metadata": _public_metadata(metadata),
                        },
                        source_event_id=source_event_id,
                    ),
                )
            return ()
        if message_type == "tool":
            signature = _tool_result_signature(str(projected.get("name", "")), content)
            if signature in self._custom_tool_results:
                self._custom_tool_results.remove(signature)
                return ()
            events = [
                self._event(
                    "tool",
                    "completed",
                    {
                        "type": "tool_result",
                        "tool": projected.get("name", ""),
                        "tool_call_id": projected.get("tool_call_id", ""),
                        "content": content,
                        "message_id": projected.get("id", ""),
                    },
                    source_event_id=source_event_id,
                )
            ]
            memory_payload = _memory_proposal_payload(
                tool_name=str(projected.get("name", "")),
                content=content,
                session_id=self.session_id,
                run_id=self.run_id,
            )
            if memory_payload is not None:
                events.append(
                    self._event(
                        "memory",
                        "completed",
                        memory_payload,
                        source_event_id=f"{source_event_id}:memory-proposal",
                    )
                )
            return tuple(events)
        return ()

    def _updates(self, payload: Any, source_event_id: str) -> tuple[RunEvent, ...]:
        if not isinstance(payload, Mapping):
            return ()
        return (
            self._event(
                "harness",
                "completed",
                {"type": "graph_update", "nodes": sorted(str(key) for key in payload)},
                source_event_id=source_event_id,
            ),
        )

    def _values(self, payload: Any, source_event_id: str) -> tuple[RunEvent, ...]:
        checkpoint_event = self._event(
            "harness",
            "completed",
            {"type": "checkpoint_update", **bounded_state_summary(payload)},
            source_event_id=source_event_id,
        )
        interrupt = _graph_interrupt(payload)
        if interrupt is None:
            return (checkpoint_event,)
        interrupt_payload, interrupt_id = interrupt
        interrupt_event_type = str(interrupt_payload.get("type", "graph_interrupt"))
        if interrupt_event_type == "approval_required":
            interrupt_payload.setdefault("interrupt_id", interrupt_id)
            return (
                self._event(
                    "approval",
                    "blocked",
                    interrupt_payload,
                    source_event_id=f"{source_event_id}:interrupt:{interrupt_id}",
                ),
                checkpoint_event,
            )
        return (
            self._event(
                "harness",
                "blocked",
                {
                    "type": "graph_interrupt",
                    "interrupt_id": interrupt_id,
                    "value": interrupt_payload,
                },
                source_event_id=f"{source_event_id}:interrupt:{interrupt_id}",
            ),
            checkpoint_event,
        )

    def _custom(self, payload: Any, source_event_id: str) -> tuple[RunEvent, ...]:
        if not isinstance(payload, Mapping):
            return ()
        event_type = str(payload.get("type", ""))
        if event_type == "memory_proposal_ready":
            memory_payload = _validated_memory_proposal_payload(
                payload,
                session_id=self.session_id,
                run_id=self.run_id,
            )
            if memory_payload is None:
                return ()
            return (
                self._event(
                    "memory",
                    "completed",
                    memory_payload,
                    source_event_id=source_event_id,
                ),
            )
        if event_type in {
            "approval_required",
            "approval_granted",
            "tool_call",
            "tool_result",
            "agent_started",
            "agent_completed",
            "subagent_started",
            "subagent_completed",
            "subagent_failed",
            "subagent_cancelled",
            "subagent_timed_out",
        }:
            event_payload = {str(key): _bounded_value(value) for key, value in payload.items()}
            if event_type.startswith("subagent"):
                event_payload.setdefault("agent_run_id", event_payload.get("child_run_id", ""))
            if event_type == "tool_call":
                tool_name = str(event_payload.get("tool", ""))
                tool_call_id = str(event_payload.get("tool_call_id", ""))
                pending = self._pending_model_calls_by_tool.get(tool_name, 0)
                if pending > 0:
                    if pending == 1:
                        self._pending_model_calls_by_tool.pop(tool_name, None)
                    else:
                        self._pending_model_calls_by_tool[tool_name] = pending - 1
                    return ()
                if tool_call_id and tool_call_id in self._seen_model_tool_calls:
                    return ()
                if tool_call_id:
                    self._seen_model_tool_calls.add(tool_call_id)
            elif event_type == "tool_result":
                self._custom_tool_results.add(
                    _tool_result_signature(
                        str(payload.get("tool", "")),
                        payload.get("content", ""),
                    )
                )
            return (
                self._event(
                    (
                        "approval"
                        if event_type.startswith("approval")
                        else "agent"
                        if event_type.startswith(("agent", "subagent"))
                        else "tool"
                    ),
                    (
                        "blocked"
                        if event_type == "approval_required"
                        else "running"
                        if event_type in {"agent_started", "subagent_started"}
                        else "cancelled"
                        if event_type == "subagent_cancelled"
                        else "error"
                        if event_type in {"subagent_failed", "subagent_timed_out"}
                        else "completed"
                    ),
                    event_payload,
                    source_event_id=source_event_id,
                ),
            )
        if event_type == "context_usage_updated":
            event_payload = {str(key): _bounded_value(value) for key, value in payload.items()}
            return (
                self._event(
                    "context",
                    "completed",
                    event_payload,
                    source_event_id=source_event_id,
                ),
            )
        safe = {str(key): _bounded_value(value) for key, value in payload.items()}
        return (
            self._event(
                "harness",
                "completed",
                {"type": "custom", "data": safe},
                source_event_id=source_event_id,
            ),
        )

    def _event(
        self,
        kind: str,
        status: str,
        payload: dict[str, Any],
        *,
        source_event_id: str,
    ) -> RunEvent:
        payload.setdefault("run_id", self.run_id)
        payload.setdefault("session_id", self.session_id)
        event_id = (
            f"{source_event_id}:public"
            if self.stream_namespace == "initial"
            else f"harness:{self.run_id}:{self.stream_namespace}:{source_event_id}:public"
        )
        return RunEvent(
            kind=kind,
            status=status,
            payload=payload,
            event_id=event_id,
        )


def _public_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"lc_agent_name", "langgraph_node", "ls_provider", "ls_model_type"}
    return {key: _bounded_value(item) for key, item in value.items() if key in allowed}


def _memory_proposal_payload(
    *,
    tool_name: str,
    content: object,
    session_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    if tool_name != "remember" or not isinstance(content, str):
        return None
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, Mapping) or value.get("status") != "pending":
        return None
    return _validated_memory_proposal_payload(
        value,
        session_id=session_id,
        run_id=run_id,
    )


def _validated_memory_proposal_payload(
    value: Mapping[object, object],
    *,
    session_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    proposal_id = value.get("proposal_id")
    reflection_id = value.get("reflection_id")
    candidate_count = value.get("candidate_count")
    base_revision = value.get("base_revision")
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        return None
    if not isinstance(reflection_id, str) or not reflection_id.strip():
        return None
    if type(candidate_count) is not int or candidate_count < 1:
        return None
    if type(base_revision) is not int or base_revision < 0:
        return None
    return {
        "type": "memory_proposal_ready",
        "session_id": session_id,
        "run_id": run_id,
        "reflection_id": reflection_id[:4000],
        "proposal_id": proposal_id[:4000],
        "candidate_count": candidate_count,
        "base_revision": base_revision,
    }


def _bounded_value(value: Any) -> Any:
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, Mapping):
        return {str(key): _bounded_value(item) for key, item in list(value.items())[:64]}
    if isinstance(value, list | tuple):
        return [_bounded_value(item) for item in list(value)[:64]]
    if isinstance(value, int | float | bool) or value is None:
        return value
    return str(value)[:4000]


def _tool_result_signature(tool_name: str, content: object) -> str:
    payload = f"{tool_name}\0{content}"
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def _graph_interrupt(value: Any) -> tuple[dict[str, Any], str] | None:
    """Project one LangGraph interrupt from a checkpoint value.

    Interrupt values are provider/tool-owned data.  Only bounded JSON-like
    fields are exposed to the Sage timeline; opaque exception objects and
    checkpoint internals never cross this boundary.
    """
    if not isinstance(value, Mapping):
        return None
    raw_interrupts = value.get("__interrupt__")
    if raw_interrupts is None:
        return None
    items = raw_interrupts if isinstance(raw_interrupts, list | tuple) else (raw_interrupts,)
    for item in items:
        raw_value = getattr(item, "value", item)
        interrupt_id = str(getattr(item, "id", "") or "")[:256]
        if not isinstance(raw_value, Mapping):
            raw_value = {"value": _bounded_value(raw_value)}
        projected = {
            str(key): _bounded_value(item_value)
            for key, item_value in list(raw_value.items())[:64]
        }
        projected.setdefault("type", "graph_interrupt")
        return projected, interrupt_id or "anonymous"
    return None


__all__ = ["HarnessEventAdapter"]
