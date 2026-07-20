"""Map public LangGraph stream items into Sage's durable run events."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from typing import Any

from sage_harness.runtime.events import (
    HarnessStreamItem,
    bounded_state_summary,
    message_payload,
)

from core.coding.run_coordinator import RunEvent

_PUBLIC_TOOL_CONTENT_LIMIT = 4_000
_PUBLIC_KNOWLEDGE_CITATION_LIMIT = 12
_PUBLIC_WEB_CITATION_LIMIT = 8
_KNOWLEDGE_STATUSES = frozenset({"evidence_found", "no_evidence", "unavailable"})
_CAPABILITY_EVENT_TYPES = frozenset(
    {
        "capability_catalog_updated",
        "capability_selected",
        "capability_selection_failed",
        "capability_invocation_completed",
    }
)
_RUN_BUDGET_NOTICES = {
    "model_call_capped": "本轮已达到模型调用安全上限，已停止继续调用工具。",
    "token_capped": "本轮已达到 token 安全上限，已停止继续调用工具。",
    "tool_call_capped": "本轮已达到工具调用安全上限，已停止继续调用工具。",
    "step_capped": "本轮已达到执行步数安全上限，已停止继续执行。",
    "time_capped": "本轮已达到执行时长安全上限，已停止继续执行。",
}
_LEGACY_PROTOCOL_TAGS = ("<tool>", "</tool>", "<final>", "</final>")


class _LegacyProtocolFilter:
    """Strip the legacy XML response protocol across arbitrary stream chunks."""

    def __init__(self) -> None:
        self._mode = "text"
        self._buffer = ""

    def feed(self, value: str, *, final: bool = False) -> str:
        self._buffer += value
        output: list[str] = []
        while self._buffer:
            if self._mode in {"tool", "final"}:
                closing = f"</{self._mode}>"
                index = self._buffer.lower().find(closing)
                if index < 0:
                    if final and self._mode == "final":
                        output.append(self._buffer)
                        self._buffer = ""
                    elif final:
                        self._buffer = ""
                    break
                if self._mode == "final":
                    output.append(self._buffer[:index])
                self._buffer = self._buffer[index + len(closing) :]
                self._mode = "text"
                continue

            lowered = self._buffer.lower()
            candidates = [
                (index, tag)
                for tag in _LEGACY_PROTOCOL_TAGS
                if (index := lowered.find(tag)) >= 0
            ]
            if candidates:
                index, tag = min(candidates, key=lambda item: item[0])
                output.append(self._buffer[:index])
                self._buffer = self._buffer[index + len(tag) :]
                self._mode = "tool" if tag == "<tool>" else "final" if tag == "<final>" else "text"
                continue
            if final:
                output.append(self._buffer)
                self._buffer = ""
                break
            keep = max(
                (
                    len(prefix)
                    for tag in _LEGACY_PROTOCOL_TAGS
                    for prefix_length in range(1, len(tag))
                    if (prefix := self._buffer[-prefix_length:]).lower() == tag[:prefix_length]
                ),
                default=0,
            )
            if keep:
                output.append(self._buffer[:-keep])
                self._buffer = self._buffer[-keep:]
                break
            else:
                output.append(self._buffer)
                self._buffer = ""
        return "".join(output)

    def finish(self) -> str:
        return self.feed("", final=True)


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
        self._pending_model_tool_calls: dict[str, dict[str, Any]] = {}
        self._custom_tool_results: set[str] = set()
        self._seen_budget_signatures: set[str] = set()
        self._seen_budget_usage_signatures: set[str] = set()
        self._assistant_protocol_filter = _LegacyProtocolFilter()

    def adapt(self, item: HarnessStreamItem) -> tuple[RunEvent, ...]:
        """Return zero or more Sage events for one graph stream item."""
        if item.mode == "messages":
            return self._messages(item.payload, item.source_event_id)
        if item.mode == "updates":
            return self._updates(item.payload, item.source_event_id)
        if item.mode == "values":
            return self._values(item.payload, item.source_event_id)
        return self._custom(item.payload, item.source_event_id)

    def finish(self) -> tuple[RunEvent, ...]:
        """Flush a trailing public fragment after the graph stream closes."""
        content = self._assistant_protocol_filter.finish()
        if not content:
            return ()
        return (
            self._event(
                "assistant",
                "running",
                {"type": "text_delta", "delta": content, "metadata": {}},
                source_event_id=f"harness:{self.run_id}:protocol-flush",
            ),
        )

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
                for call in tool_calls:
                    if not isinstance(call, Mapping):
                        continue
                    name = str(call.get("name", ""))
                    tool_call_id = str(call.get("id", ""))
                    args = call.get("args", {})
                    if not name or not tool_call_id or tool_call_id in self._seen_model_tool_calls:
                        continue
                    self._pending_model_tool_calls[tool_call_id] = {
                        "type": "tool_call",
                        "tool": name,
                        "args": args if isinstance(args, Mapping) else {},
                        "tool_call_id": tool_call_id,
                        "message_id": projected.get("id", ""),
                    }
                return ()
            content = self._assistant_protocol_filter.feed(str(content))
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
            tool_name = str(projected.get("name", ""))
            content = _public_tool_result_content(tool_name, content)
            signature = _tool_result_signature(tool_name, content)
            if signature in self._custom_tool_results:
                self._custom_tool_results.remove(signature)
                return ()
            is_error = projected.get("status") == "error"
            events: list[RunEvent] = []
            pending_call = self._take_pending_tool_call(
                tool_call_id=str(projected.get("tool_call_id", "")),
                tool_name=tool_name,
                source_event_id=f"{source_event_id}:late-call",
            )
            if pending_call is not None:
                events.append(pending_call)
            events.append(
                self._event(
                    "tool",
                    "error" if is_error else "completed",
                    _tool_result_payload(projected, tool_name, content, is_error),
                    source_event_id=source_event_id,
                )
            )
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
            source_proposal_payload = _knowledge_source_proposal_payload(
                tool_name=tool_name,
                content=content,
                session_id=self.session_id,
                run_id=self.run_id,
            )
            if source_proposal_payload is not None:
                events.append(
                    self._event(
                        "proposal",
                        "pending",
                        source_proposal_payload,
                        source_event_id=f"{source_event_id}:knowledge-source-proposal",
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
        budget_events = (
            *self._budget_usage_from_state(payload, source_event_id),
            *self._budget_from_state(payload, source_event_id),
        )
        if interrupt is None:
            return (*budget_events, checkpoint_event)
        interrupt_payload, interrupt_id = interrupt
        interrupt_event_type = str(interrupt_payload.get("type", "graph_interrupt"))
        if interrupt_event_type == "approval_required":
            interrupt_payload.setdefault("interrupt_id", interrupt_id)
            approval_call = self._tool_call_event(
                tool_call_id=str(interrupt_payload.get("tool_call_id", "")),
                tool_name=str(interrupt_payload.get("tool", "")),
                args=interrupt_payload.get("args", {}),
                source_event_id=f"{source_event_id}:approval-call",
            )
            approval_events = (
                self._event(
                    "approval",
                    "blocked",
                    interrupt_payload,
                    source_event_id=f"{source_event_id}:interrupt:{interrupt_id}",
                ),
                checkpoint_event,
            )
            if approval_call is None:
                return (*budget_events, *approval_events)
            return (*budget_events, approval_call, *approval_events)
        return (
            *budget_events,
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
        if event_type == "run_budget_exhausted":
            return self._budget_events(payload, source_event_id)
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
        if event_type in _CAPABILITY_EVENT_TYPES:
            capability_payload = _public_capability_payload(payload)
            if capability_payload is None:
                return ()
            return (
                self._event(
                    "harness",
                    (
                        "error"
                        if event_type == "capability_selection_failed"
                        or capability_payload.get("status") == "failure"
                        else "completed"
                    ),
                    capability_payload,
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
            "subagent_progress",
        }:
            event_payload = (
                _public_subagent_progress(payload)
                if event_type == "subagent_progress"
                else {str(key): _bounded_value(value) for key, value in payload.items()}
            )
            if event_type.startswith("subagent"):
                event_payload.setdefault("agent_run_id", event_payload.get("child_run_id", ""))
            if event_type == "tool_call":
                tool_name = str(event_payload.get("tool", ""))
                tool_call_id = str(event_payload.get("tool_call_id", ""))
                call_event = self._tool_call_event(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args=event_payload.get("args", {}),
                    source_event_id=source_event_id,
                )
                return () if call_event is None else (call_event,)
            elif event_type == "tool_result":
                tool_name = str(payload.get("tool", ""))
                tool_call_id = str(payload.get("tool_call_id", ""))
                call_event = self._tool_call_event(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    args=payload.get("args", {}),
                    source_event_id=f"{source_event_id}:late-call",
                )
                public_content = _public_tool_result_content(
                    tool_name,
                    payload.get("content", ""),
                )
                event_payload["content"] = public_content
                self._custom_tool_results.add(
                    _tool_result_signature(
                        tool_name,
                        public_content,
                    )
                )
                result_event = self._event(
                    "tool",
                    "error" if bool(event_payload.get("is_error")) else "completed",
                    event_payload,
                    source_event_id=source_event_id,
                )
                return (
                    *((call_event,) if call_event is not None else ()),
                    result_event,
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
                        if event_type
                        in {"agent_started", "subagent_started", "subagent_progress"}
                        else "error"
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

    def _budget_from_state(
        self,
        payload: Any,
        source_event_id: str,
    ) -> tuple[RunEvent, ...]:
        if not isinstance(payload, Mapping):
            return ()
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return ()
        projected = message_payload(messages[-1])
        harness_meta = projected.get("sage_harness")
        if not isinstance(harness_meta, Mapping):
            return ()
        return self._budget_events(
            {"type": "run_budget_exhausted", **harness_meta},
            f"{source_event_id}:budget",
        )

    def _budget_usage_from_state(
        self,
        payload: Any,
        source_event_id: str,
    ) -> tuple[RunEvent, ...]:
        if not isinstance(payload, Mapping):
            return ()
        used_tokens = _public_non_negative_int(
            payload.get("run_token_usage")
        ) + _public_non_negative_int(payload.get("run_child_token_usage"))
        limit_tokens = _public_non_negative_int(payload.get("run_token_limit"))
        model_calls = _public_non_negative_int(
            payload.get("run_model_calls")
        ) + _public_non_negative_int(payload.get("run_child_model_calls"))
        model_call_limit = _public_non_negative_int(payload.get("run_model_call_limit"))
        tool_calls = _public_non_negative_int(
            payload.get("run_tool_calls")
        ) + _public_non_negative_int(payload.get("run_child_tool_calls"))
        tool_call_limit = _public_non_negative_int(payload.get("run_tool_call_limit"))
        if limit_tokens <= 0 or model_call_limit <= 0 or tool_call_limit <= 0:
            return ()
        signature = ":".join(
            str(item)
            for item in (
                used_tokens,
                limit_tokens,
                model_calls,
                model_call_limit,
                tool_calls,
                tool_call_limit,
            )
        )
        if signature in self._seen_budget_usage_signatures:
            return ()
        self._seen_budget_usage_signatures.add(signature)
        return (
            self._event(
                "harness",
                "completed",
                {
                    "type": "run_budget_updated",
                    "used_tokens": used_tokens,
                    "limit_tokens": limit_tokens,
                    "model_calls": model_calls,
                    "model_call_limit": model_call_limit,
                    "tool_calls": tool_calls,
                    "tool_call_limit": tool_call_limit,
                    "usage_ratio": min(1.0, used_tokens / limit_tokens),
                },
                source_event_id=f"{source_event_id}:budget-usage",
            ),
        )

    def _budget_events(
        self,
        payload: Mapping[str, Any],
        source_event_id: str,
    ) -> tuple[RunEvent, ...]:
        stop_reason = str(payload.get("stop_reason", ""))
        notice = _RUN_BUDGET_NOTICES.get(stop_reason)
        if notice is None:
            return ()
        used = _public_number(payload.get("used"))
        limit = _public_number(payload.get("limit"))
        signature = f"{stop_reason}:{used}:{limit}"
        if signature in self._seen_budget_signatures:
            return ()
        self._seen_budget_signatures.add(signature)
        self._pending_model_tool_calls.clear()
        return (
            self._event(
                "harness",
                "completed",
                {
                    "type": "run_budget_exhausted",
                    "stop_reason": stop_reason,
                    "used": used,
                    "limit": limit,
                },
                source_event_id=f"{source_event_id}:event",
            ),
            self._event(
                "assistant",
                "running",
                {
                    "type": "text_delta",
                    "delta": notice,
                    "metadata": {"stop_reason": stop_reason},
                },
                source_event_id=f"{source_event_id}:notice",
            ),
        )

    def _take_pending_tool_call(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        source_event_id: str,
    ) -> RunEvent | None:
        pending_id = tool_call_id if tool_call_id in self._pending_model_tool_calls else ""
        if not pending_id:
            pending_id = next(
                (
                    call_id
                    for call_id, call in self._pending_model_tool_calls.items()
                    if str(call.get("tool", "")) == tool_name
                ),
                "",
            )
        if not pending_id:
            return None
        payload = self._pending_model_tool_calls.pop(pending_id)
        if pending_id in self._seen_model_tool_calls:
            return None
        self._seen_model_tool_calls.add(pending_id)
        return self._event(
            "tool",
            "running",
            payload,
            source_event_id=source_event_id,
        )

    def _tool_call_event(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        args: object,
        source_event_id: str,
    ) -> RunEvent | None:
        pending = self._take_pending_tool_call(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            source_event_id=source_event_id,
        )
        if pending is not None:
            return pending
        if not tool_call_id or not tool_name or tool_call_id in self._seen_model_tool_calls:
            return None
        self._seen_model_tool_calls.add(tool_call_id)
        return self._event(
            "tool",
            "running",
            {
                "type": "tool_call",
                "tool": tool_name,
                "args": _bounded_value(args) if isinstance(args, Mapping) else {},
                "tool_call_id": tool_call_id,
                "message_id": "",
            },
            source_event_id=source_event_id,
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


def _public_assistant_content(value: object) -> object:
    """Hide legacy protocol blocks from non-streaming browser timeline content."""
    if not isinstance(value, str):
        return value
    return _LegacyProtocolFilter().feed(value, final=True).strip()


def _tool_result_payload(
    projected: Mapping[str, Any],
    tool_name: str,
    content: str,
    is_error: bool,
) -> dict[str, Any]:
    payload = {
        "type": "tool_result",
        "tool": tool_name,
        "tool_call_id": projected.get("tool_call_id", ""),
        "content": content,
        "message_id": projected.get("id", ""),
        "is_error": is_error,
    }
    artifact = projected.get("artifact")
    if isinstance(artifact, Mapping):
        artifact_ref = artifact.get("artifact_ref")
        if isinstance(artifact_ref, str) and artifact_ref.startswith("sage://coding/"):
            payload.update(
                {
                    "artifact_ref": artifact_ref,
                    "original_chars": _public_non_negative_int(
                        artifact.get("original_chars")
                    ),
                    "truncated": artifact.get("truncated") is True,
                }
            )
    subagent = projected.get("sage_subagent")
    if tool_name == "task" and isinstance(subagent, Mapping):
        operation_id = _public_string(subagent.get("child_run_id"), 256)
        payload["subagent"] = {
            "child_run_id": operation_id,
            "parent_run_id": _public_string(subagent.get("parent_run_id"), 256),
            "status": _public_string(subagent.get("status"), 32),
            "result_ref": _public_string(subagent.get("result_ref"), 1_000),
            "error_code": _public_string(subagent.get("error_code"), 128),
            "evidence_count": _public_non_negative_int(subagent.get("evidence_count")),
            "token_usage": _public_non_negative_int(subagent.get("token_usage")),
            "model_calls": _public_non_negative_int(subagent.get("model_calls")),
            "tool_count": _public_non_negative_int(subagent.get("tool_count")),
        }
        if operation_id:
            payload["operation_ref"] = {"kind": "coding_run", "id": operation_id}
    return payload


def _public_number(value: object) -> int | float:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            return 0
        return max(value, 0)
    return 0


def _public_capability_payload(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    event_type = str(payload.get("type", ""))
    version = payload.get("version")
    revision = _public_string(payload.get("catalog_revision"), 128)
    if event_type not in _CAPABILITY_EVENT_TYPES or version != 1 or not revision:
        return None
    public: dict[str, Any] = {
        "type": event_type,
        "version": 1,
        "catalog_revision": revision,
    }
    catalog_hash = _public_string(payload.get("catalog_hash"), 128)
    if catalog_hash:
        public["catalog_hash"] = catalog_hash
    if event_type == "capability_catalog_updated":
        public.update(
            {
                "surface": _public_string(payload.get("surface"), 32),
                "capability_count": _public_non_negative_int(
                    payload.get("capability_count")
                ),
                "executable_count": _public_non_negative_int(
                    payload.get("executable_count")
                ),
            }
        )
        return public
    if event_type == "capability_selected":
        capability_ids = _public_capability_ids(payload.get("capability_ids"))
        if not capability_ids:
            return None
        public.update(
            {
                "capability_ids": capability_ids,
                "selected_count": len(capability_ids),
            }
        )
        return public
    if event_type == "capability_selection_failed":
        categories = _public_failure_categories(
            payload.get("failure_categories"),
            allowed={
                "ambiguous",
                "disallowed",
                "not_deferred",
                "surface_mismatch",
                "unavailable",
                "unknown",
            },
        )
        if not categories:
            return None
        public.update(
            {
                "failure_categories": categories,
                "rejected_count": _public_non_negative_int(
                    payload.get("rejected_count")
                ),
            }
        )
        return public
    capability_id = _public_string(payload.get("capability_id"), 256)
    status = _public_string(payload.get("status"), 16)
    if not capability_id or status not in {"success", "failure"}:
        return None
    public.update(
        {
            "capability_id": capability_id,
            "status": status,
            "duration_ms": _public_non_negative_int(payload.get("duration_ms")),
        }
    )
    if status == "failure":
        category = _public_string(payload.get("failure_category"), 64)
        if category not in {
            "approval_denied",
            "execution_error",
            "policy_blocked",
            "timeout",
            "tool_error",
            "unavailable",
        }:
            category = "execution_error"
        public["failure_category"] = category
    return public


def _public_subagent_progress(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Project child progress without exposing prompts, arguments, or tool output."""
    phase = _public_string(payload.get("phase"), 32)
    if phase not in {
        "model_requested",
        "tool_started",
        "tool_completed",
        "approval_required",
    }:
        phase = "model_requested"
    status = _public_string(payload.get("status"), 16)
    if status not in {"running", "waiting", "completed", "error"}:
        status = "running"
    public: dict[str, Any] = {
        "type": "subagent_progress",
        "child_run_id": _public_string(payload.get("child_run_id"), 256),
        "parent_run_id": _public_string(payload.get("parent_run_id"), 256),
        "subagent_type": _public_string(payload.get("subagent_type"), 64),
        "phase": phase,
        "status": status,
        "tool_count": _public_non_negative_int(payload.get("tool_count")),
        "evidence_count": _public_non_negative_int(payload.get("evidence_count")),
    }
    tool_name = _public_string(payload.get("tool"), 128)
    if tool_name:
        public["tool"] = tool_name
    operation_ref = payload.get("operation_ref")
    if isinstance(operation_ref, Mapping):
        kind = _public_string(operation_ref.get("kind"), 64)
        identifier = _public_string(operation_ref.get("id"), 256)
        if kind and identifier:
            public["operation_ref"] = {"kind": kind, "id": identifier}
    return public


def _public_capability_ids(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [
        item
        for item in (_public_string(candidate, 256) for candidate in value[:20])
        if item
    ]


def _public_failure_categories(
    value: object,
    *,
    allowed: set[str],
) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return sorted(
        {
            category
            for category in (_public_string(candidate, 64) for candidate in value[:20])
            if category in allowed
        }
    )


def _public_tool_result_content(tool_name: str, value: object) -> str:
    text = value if isinstance(value, str) else str(value)
    if tool_name == "search_web":
        return _public_web_search_content(text)
    if tool_name == "fetch_web":
        return _public_web_fetch_content(text)
    if tool_name != "knowledge_search":
        return text[:_PUBLIC_TOOL_CONTENT_LIMIT]
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return text[:_PUBLIC_TOOL_CONTENT_LIMIT]
    if not isinstance(payload, Mapping):
        return text[:_PUBLIC_TOOL_CONTENT_LIMIT]
    status = str(payload.get("status", ""))
    if status not in _KNOWLEDGE_STATUSES:
        return text[:_PUBLIC_TOOL_CONTENT_LIMIT]

    raw_citations = payload.get("citations", [])
    raw_citation_items = raw_citations if isinstance(raw_citations, list) else []
    citations = [
        citation
        for citation in raw_citation_items
        if isinstance(citation, Mapping)
        and _public_string(citation.get("citation_id"), 160)
        and _public_string(citation.get("page_revision"), 160)
        and _public_string(citation.get("excerpt"), 1)
    ]
    omitted_count = _public_non_negative_int(payload.get("omitted_count"))
    base = {
        "status": status,
        "query": _public_string(payload.get("query"), 400),
        "used_tokens": _public_non_negative_int(payload.get("used_tokens")),
        "token_budget": _public_non_negative_int(payload.get("token_budget")),
        "omitted_count": omitted_count,
    }
    if status != "evidence_found" or not citations:
        return json.dumps(
            {**base, "citations": []},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    maximum = min(len(citations), _PUBLIC_KNOWLEDGE_CITATION_LIMIT)
    raw_count = len(raw_citation_items)
    for count in range(maximum, 0, -1):
        for excerpt_limit in (800, 400, 200, 100, 40):
            public_citations = [
                _public_knowledge_citation(item, excerpt_limit=excerpt_limit)
                for item in citations[:count]
            ]
            candidate = {
                **base,
                "omitted_count": omitted_count + max(0, raw_count - count),
                "citations": public_citations,
            }
            encoded = json.dumps(
                candidate,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if len(encoded) <= _PUBLIC_TOOL_CONTENT_LIMIT:
                return encoded

    first = citations[0]
    minimal = {
        **base,
        "omitted_count": omitted_count + max(0, raw_count - 1),
        "citations": [
            {
                "citation_id": _public_string(first.get("citation_id"), 160),
                "rank": _public_positive_int(first.get("rank")) or 1,
                "page_revision": _public_string(first.get("page_revision"), 160),
                "excerpt": _public_string(first.get("excerpt"), 40),
                "truncated": True,
            }
        ],
    }
    return json.dumps(minimal, ensure_ascii=False, separators=(",", ":"))


def _public_web_search_content(text: str) -> str:
    payload = _remote_json_payload(text)
    if payload is None:
        return text[:_PUBLIC_TOOL_CONTENT_LIMIT]
    status = _public_string(payload.get("status"), 32)
    if status not in _KNOWLEDGE_STATUSES:
        return text[:_PUBLIC_TOOL_CONTENT_LIMIT]
    raw_citations = payload.get("citations")
    citations = [item for item in raw_citations if isinstance(item, Mapping)][
        :_PUBLIC_WEB_CITATION_LIMIT
    ] if isinstance(raw_citations, list) else []
    omitted_count = _public_non_negative_int(payload.get("omitted_count"))
    base = {
        "status": status,
        "query": _public_string(payload.get("query"), 400),
        "provider": _public_string(payload.get("provider"), 80),
        "used_tokens": _public_non_negative_int(payload.get("used_tokens")),
        "token_budget": _public_non_negative_int(payload.get("token_budget")),
        "omitted_count": omitted_count,
        "error_code": _public_string(payload.get("error_code"), 80),
        "remote_content": True,
    }
    if status != "evidence_found" or not citations:
        return json.dumps({**base, "citations": []}, ensure_ascii=False, separators=(",", ":"))
    for count in range(len(citations), 0, -1):
        for excerpt_limit in (700, 350, 160, 80, 40):
            public_citations = [
                {
                    "citation_id": _public_string(item.get("citation_id"), 160),
                    "rank": _public_positive_int(item.get("rank")),
                    "url": _public_string(item.get("url"), 1_000),
                    "title": _public_string(item.get("title"), 240),
                    "excerpt": _public_string(item.get("excerpt"), excerpt_limit),
                    "provider": _public_string(item.get("provider"), 80),
                    "retrieved_at": _public_string(item.get("retrieved_at"), 80),
                    "content_hash": _public_string(item.get("content_hash"), 128),
                    "remote_content": True,
                }
                for item in citations[:count]
                if _public_string(item.get("citation_id"), 160)
                and _public_string(item.get("url"), 1)
            ]
            candidate = {
                **base,
                "omitted_count": omitted_count + max(0, len(citations) - count),
                "citations": public_citations,
            }
            encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
            if len(encoded) <= _PUBLIC_TOOL_CONTENT_LIMIT:
                return encoded
    return json.dumps({**base, "citations": []}, ensure_ascii=False, separators=(",", ":"))


def _public_web_fetch_content(text: str) -> str:
    payload = _remote_json_payload(text)
    if payload is None:
        return text[:_PUBLIC_TOOL_CONTENT_LIMIT]
    status = _public_string(payload.get("status"), 32)
    if status not in {"evidence_found", "unavailable"}:
        return text[:_PUBLIC_TOOL_CONTENT_LIMIT]
    artifact_ref = _public_string(payload.get("artifact_ref"), 1_000)
    if artifact_ref and not artifact_ref.startswith("sage://coding/"):
        artifact_ref = ""
    public = {
        "status": status,
        "citation_id": _public_string(payload.get("citation_id"), 160),
        "url": _public_string(payload.get("url"), 1_000),
        "title": _public_string(payload.get("title"), 240),
        "excerpt": _public_string(payload.get("excerpt"), 1_200),
        "retrieved_at": _public_string(payload.get("retrieved_at"), 80),
        "content_hash": _public_string(payload.get("content_hash"), 128),
        "media_type": _public_string(payload.get("media_type"), 80),
        "wire_bytes": _public_non_negative_int(payload.get("wire_bytes")),
        "used_tokens": _public_non_negative_int(payload.get("used_tokens")),
        "token_budget": _public_non_negative_int(payload.get("token_budget")),
        "artifact_ref": artifact_ref,
        "original_chars": _public_non_negative_int(payload.get("original_chars")),
        "error_code": _public_string(payload.get("error_code"), 80),
        "remote_content": True,
    }
    for excerpt_limit in (1_200, 800, 400, 160, 80, 40, 0):
        candidate = {
            **public,
            "excerpt": _public_string(payload.get("excerpt"), excerpt_limit)
            if excerpt_limit
            else "",
        }
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) <= _PUBLIC_TOOL_CONTENT_LIMIT:
            return encoded
    return json.dumps(
        {"status": status, "remote_content": True},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _remote_json_payload(text: str) -> Mapping[object, object] | None:
    candidate = text.strip()
    boundaries = (
        ("<remote-content>", "</remote-content>"),
        ("--- BEGIN REMOTE TOOL CONTENT ---", "--- END REMOTE TOOL CONTENT ---"),
    )
    for prefix, suffix in boundaries:
        if candidate.startswith(prefix) and candidate.endswith(suffix):
            candidate = candidate[len(prefix) : -len(suffix)].strip()
            break
    try:
        payload = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _public_knowledge_citation(
    value: Mapping[object, object],
    *,
    excerpt_limit: int,
) -> dict[str, object]:
    excerpt = _public_string(value.get("excerpt"), excerpt_limit)
    original_excerpt = value.get("excerpt")
    heading_path = value.get("heading_path")
    return {
        "citation_id": _public_string(value.get("citation_id"), 160),
        "rank": _public_positive_int(value.get("rank")),
        "page_revision": _public_string(value.get("page_revision"), 160),
        "source_revision": _public_string(value.get("source_revision"), 160),
        "source_kind": _public_string(value.get("source_kind"), 80),
        "source_relative_path": _public_string(value.get("source_relative_path"), 500),
        "title": _public_string(value.get("title"), 240),
        "heading_path": [
            _public_string(item, 160) for item in heading_path[:8] if _public_string(item, 160)
        ]
        if isinstance(heading_path, list)
        else [],
        "block_id": _public_string(value.get("block_id"), 160),
        "excerpt": excerpt,
        "truncated": bool(value.get("truncated"))
        or (isinstance(original_excerpt, str) and len(original_excerpt.strip()) > len(excerpt)),
    }


def _public_string(value: object, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _public_non_negative_int(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def _public_positive_int(value: object) -> int:
    return value if type(value) is int and value > 0 else 0


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


def _knowledge_source_proposal_payload(
    *,
    tool_name: str,
    content: object,
    session_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    if tool_name != "save_web_source" or not isinstance(content, str):
        return None
    try:
        value = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, Mapping):
        return None
    proposal_id = _public_string(value.get("proposal_id"), 128)
    content_hash = _public_string(value.get("content_hash"), 64)
    revision = _public_non_negative_int(value.get("revision"))
    if (
        not proposal_id.startswith("ksprop_")
        or len(content_hash) != 64
        or revision < 1
        or value.get("status") != "pending"
    ):
        return None
    return {
        "type": "knowledge_source_proposal_created",
        "proposal_id": proposal_id,
        "proposal_type": "knowledge_source",
        "source_kind": "web",
        "content_hash": content_hash,
        "requires_user_confirmation": True,
        "revision": revision,
        "session_id": session_id,
        "run_id": run_id,
    }


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
