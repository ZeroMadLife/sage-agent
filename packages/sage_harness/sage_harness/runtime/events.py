"""Stable, bounded envelopes for LangGraph stream modes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

StreamMode = Literal["messages", "updates", "values", "custom"]
_TOOL_MESSAGE_CONTENT_LIMIT = 128_000


@dataclass(frozen=True, slots=True)
class HarnessStreamItem:
    """One normalized graph stream item with an idempotency key."""

    sequence: int
    mode: StreamMode
    payload: Any
    source_event_id: str


def normalize_stream_item(raw: Any, sequence: int) -> HarnessStreamItem:
    """Normalize LangGraph's tuple/non-tuple stream forms without full state dumps."""
    if sequence < 1:
        raise ValueError("stream sequence must be positive")
    mode: str = "values"
    payload = raw
    if isinstance(raw, tuple) and len(raw) == 2:
        mode, payload = str(raw[0]), raw[1]
    if mode not in {"messages", "updates", "values", "custom"}:
        mode = "custom"
    stream_mode = mode  # narrowed after the explicit allow-list above
    identifier = _payload_identifier(stream_mode, payload)
    source_event_id = f"graph:{stream_mode}:{sequence}:{identifier}"
    return HarnessStreamItem(
        sequence=sequence,
        mode=stream_mode,  # type: ignore[arg-type]
        payload=payload,
        source_event_id=source_event_id,
    )


def message_payload(message: Any) -> dict[str, Any]:
    """Project a LangChain message to public metadata and bounded content."""
    if not isinstance(message, BaseMessage):
        return {"type": "unknown", "content": _bounded_text(message)}
    if isinstance(message, AIMessage):
        message_type = "ai"
    elif isinstance(message, ToolMessage):
        message_type = "tool"
    else:
        message_type = str(getattr(message, "type", "message"))
    content = getattr(message, "content", "")
    name = getattr(message, "name", None)
    projected: dict[str, Any] = {
        "type": message_type,
        "id": str(getattr(message, "id", "") or ""),
        "content": _bounded_content(
            content,
            text_limit=(
                _TOOL_MESSAGE_CONTENT_LIMIT
                if isinstance(message, ToolMessage)
                and name in {"knowledge_search", "search_web"}
                else 4_000
            ),
        ),
    }
    if name:
        projected["name"] = str(name)
    tool_call_id = getattr(message, "tool_call_id", None)
    if tool_call_id:
        projected["tool_call_id"] = str(tool_call_id)
    status = getattr(message, "status", None)
    if status:
        projected["status"] = str(status)
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        projected["tool_calls"] = _bounded_json(tool_calls)
    usage = getattr(message, "usage_metadata", None)
    if usage:
        projected["usage_metadata"] = _bounded_json(usage)
    artifact = getattr(message, "artifact", None)
    if isinstance(message, ToolMessage) and isinstance(artifact, dict):
        artifact_ref = artifact.get("artifact_ref")
        if isinstance(artifact_ref, str) and artifact_ref.strip():
            projected["artifact"] = {
                "artifact_ref": artifact_ref[:1_000],
                "original_chars": _non_negative_int(artifact.get("original_chars")),
                "truncated": artifact.get("truncated") is True,
            }
    harness_meta = message.additional_kwargs.get("sage_harness")
    if isinstance(harness_meta, dict):
        projected["sage_harness"] = {
            key: _bounded_json(harness_meta[key])
            for key in ("stop_reason", "used", "limit", "notice")
            if key in harness_meta
        }
    subagent_meta = message.additional_kwargs.get("sage_subagent")
    if isinstance(message, ToolMessage) and isinstance(subagent_meta, dict):
        projected["sage_subagent"] = {
            key: _bounded_json(subagent_meta[key])
            for key in (
                "child_run_id",
                "parent_run_id",
                "status",
                "result_ref",
                "error_code",
                "evidence_count",
                "token_usage",
                "model_calls",
                "tool_count",
            )
            if key in subagent_meta
        }
    return projected


def bounded_state_summary(value: Any) -> dict[str, Any]:
    """Expose checkpoint shape, not checkpoint contents, to the browser timeline."""
    if not isinstance(value, dict):
        return {"state_type": type(value).__name__}
    summary: dict[str, Any] = {"channels": sorted(str(key) for key in value)}
    messages = value.get("messages")
    if isinstance(messages, list):
        summary["message_count"] = len(messages)
        if messages:
            summary["last_message_type"] = str(getattr(messages[-1], "type", "message"))
    return summary


def _payload_identifier(mode: str, payload: Any) -> str:
    if mode == "messages" and isinstance(payload, tuple) and payload:
        message = payload[0]
        message_id = str(getattr(message, "id", "") or "")
        if message_id:
            return message_id
    if mode == "updates" and isinstance(payload, dict):
        return ",".join(sorted(str(key) for key in payload)) or "update"
    return _digest(payload)


def _digest(value: Any) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, default=str, ensure_ascii=True)
    except (TypeError, ValueError):
        encoded = repr(value)
    return hashlib.sha256(encoded.encode("utf-8", "replace")).hexdigest()[:16]


def _bounded_text(value: Any, limit: int = 4000) -> str:
    return str(value)[:limit]


def _bounded_content(value: Any, *, text_limit: int = 4_000) -> Any:
    if isinstance(value, str):
        return _bounded_text(value, text_limit)
    if isinstance(value, list):
        return [_bounded_json(item) for item in value[:32]]
    return _bounded_json(value)


def _bounded_json(value: Any) -> Any:
    if isinstance(value, str):
        return _bounded_text(value)
    if isinstance(value, dict):
        return {str(key): _bounded_json(item) for key, item in list(value.items())[:64]}
    if isinstance(value, list | tuple):
        return [_bounded_json(item) for item in list(value)[:64]]
    if isinstance(value, int | float | bool) or value is None:
        return value
    return _bounded_text(value)


def _non_negative_int(value: object) -> int:
    return max(value, 0) if isinstance(value, int) and not isinstance(value, bool) else 0


__all__ = [
    "HarnessStreamItem",
    "bounded_state_summary",
    "message_payload",
    "normalize_stream_item",
]
