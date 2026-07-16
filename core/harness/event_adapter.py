"""Map public LangGraph stream items into Sage's durable run events."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sage_harness.runtime.events import (
    HarnessStreamItem,
    bounded_state_summary,
    message_payload,
)

from core.coding.run_coordinator import RunEvent


class HarnessEventAdapter:
    """Translate graph events without persisting private model reasoning."""

    def __init__(self, *, session_id: str, run_id: str) -> None:
        if not session_id.strip() or not run_id.strip():
            raise ValueError("session_id and run_id are required")
        self.session_id = session_id
        self.run_id = run_id

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
            if projected.get("tool_calls"):
                return (
                    self._event(
                        "tool",
                        "running",
                        {
                            "type": "tool_call",
                            "tool_calls": projected["tool_calls"],
                            "message_id": projected.get("id", ""),
                        },
                        source_event_id=source_event_id,
                    ),
                )
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
            return (
                self._event(
                    "tool",
                    "completed",
                    {
                        "type": "tool_result",
                        "tool": projected.get("name", ""),
                        "content": content,
                        "message_id": projected.get("id", ""),
                    },
                    source_event_id=source_event_id,
                ),
            )
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
        return (
            self._event(
                "harness",
                "completed",
                {"type": "checkpoint_update", **bounded_state_summary(payload)},
                source_event_id=source_event_id,
            ),
        )

    def _custom(self, payload: Any, source_event_id: str) -> tuple[RunEvent, ...]:
        if not isinstance(payload, Mapping):
            return ()
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
        return RunEvent(
            kind=kind,
            status=status,
            payload=payload,
            event_id=f"{source_event_id}:public",
        )


def _public_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"lc_agent_name", "langgraph_node", "ls_provider", "ls_model_type"}
    return {key: _bounded_value(item) for key, item in value.items() if key in allowed}


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


__all__ = ["HarnessEventAdapter"]
