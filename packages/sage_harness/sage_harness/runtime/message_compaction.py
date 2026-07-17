"""Checkpoint-safe message compaction driven by a host-provided summary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from langchain_core.messages import AIMessage, BaseMessage, RemoveMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from sage_harness.config import HarnessRunContext
from sage_harness.runtime.checkpoint import load_scoped_checkpoint, thread_config

_MAX_SUMMARY_CHARS = 8_000
_MAX_COMPACTION_ID_CHARS = 128
_MAX_RECENT_MESSAGES = 32


class GraphMessageCompactionError(RuntimeError):
    """The graph checkpoint could not be safely prepared for compaction."""


@dataclass(frozen=True, slots=True)
class GraphMessageCompactionRequest:
    """Bounded server-owned instruction to replace old graph messages."""

    compaction_id: str
    summary_text: str
    keep_recent_messages: int = 12

    def __post_init__(self) -> None:
        compaction_id = self.compaction_id.strip()
        summary_text = self.summary_text.strip()
        if not compaction_id:
            raise ValueError("compaction_id must not be empty")
        if not summary_text:
            raise ValueError("summary_text must not be empty")
        if not 1 <= self.keep_recent_messages <= _MAX_RECENT_MESSAGES:
            raise ValueError(
                f"keep_recent_messages must be between 1 and {_MAX_RECENT_MESSAGES}"
            )
        object.__setattr__(self, "compaction_id", compaction_id[:_MAX_COMPACTION_ID_CHARS])
        object.__setattr__(self, "summary_text", summary_text[:_MAX_SUMMARY_CHARS])


@dataclass(frozen=True, slots=True)
class GraphMessageCompactionPlan:
    """Message reducer update plus public, content-free audit counts."""

    compaction_id: str
    summary_text: str
    message_updates: tuple[BaseMessage, ...]
    removed_message_count: int
    preserved_message_count: int

    def state_update(self) -> dict[str, object]:
        return {
            "messages": list(self.message_updates),
            "summary_text": self.summary_text,
        }


def build_graph_message_compaction_plan(
    messages: list[BaseMessage],
    request: GraphMessageCompactionRequest,
) -> GraphMessageCompactionPlan:
    """Keep a safe graph tail while replacing everything else with summary state."""
    if any(not isinstance(message, BaseMessage) for message in messages):
        raise GraphMessageCompactionError("graph checkpoint contains invalid messages")
    if any(isinstance(message, RemoveMessage) for message in messages):
        raise GraphMessageCompactionError("graph checkpoint contains unresolved removals")

    cutoff = max(0, len(messages) - request.keep_recent_messages)
    cutoff = _protect_tool_boundary(messages, cutoff)
    cutoff = min(cutoff, _earliest_unresolved_tool_call(messages, fallback=cutoff))
    preserved = tuple(
        _ensure_message_id(message, request.compaction_id, index)
        for index, message in enumerate(messages[cutoff:], start=cutoff)
    )
    return GraphMessageCompactionPlan(
        compaction_id=request.compaction_id,
        summary_text=request.summary_text,
        message_updates=(RemoveMessage(id=REMOVE_ALL_MESSAGES), *preserved),
        removed_message_count=cutoff,
        preserved_message_count=len(preserved),
    )


async def load_graph_message_compaction_plan(
    checkpointer: BaseCheckpointSaver[Any],
    *,
    thread_id: str,
    request: GraphMessageCompactionRequest,
    context: HarnessRunContext | None = None,
) -> GraphMessageCompactionPlan:
    """Read one thread checkpoint and build a reducer update without rewriting storage directly."""
    try:
        if context is not None:
            if context.thread_id != thread_id:
                raise GraphMessageCompactionError("graph compaction scope is invalid")
            checkpoint_tuple = await load_scoped_checkpoint(checkpointer, context)
        else:
            checkpoint_tuple = await checkpointer.aget_tuple(
                cast(RunnableConfig, thread_config(thread_id))
            )
    except Exception as exc:
        raise GraphMessageCompactionError("graph checkpoint could not be read") from exc
    if checkpoint_tuple is None:
        messages: list[BaseMessage] = []
    else:
        checkpoint = getattr(checkpoint_tuple, "checkpoint", {}) or {}
        channel_values = checkpoint.get("channel_values", {})
        raw_messages = channel_values.get("messages", []) if isinstance(channel_values, dict) else []
        if not isinstance(raw_messages, list):
            raise GraphMessageCompactionError("graph checkpoint messages are invalid")
        messages = []
        for message in raw_messages:
            if not isinstance(message, BaseMessage):
                raise GraphMessageCompactionError(
                    "graph checkpoint contains invalid messages"
                )
            messages.append(message)
    return build_graph_message_compaction_plan(messages, request)


def _protect_tool_boundary(messages: list[BaseMessage], cutoff: int) -> int:
    """Move a cutoff backwards when it would orphan ToolMessages from their AI call."""
    if cutoff >= len(messages) or not isinstance(messages[cutoff], ToolMessage):
        return cutoff
    tool_call_ids: set[str] = set()
    index = cutoff
    while index < len(messages) and isinstance(messages[index], ToolMessage):
        tool_message = messages[index]
        assert isinstance(tool_message, ToolMessage)
        tool_call_id = tool_message.tool_call_id
        if tool_call_id:
            tool_call_ids.add(tool_call_id)
        index += 1
    for candidate in range(cutoff - 1, -1, -1):
        message = messages[candidate]
        if not isinstance(message, AIMessage):
            continue
        ids = {
            str(call.get("id"))
            for call in message.tool_calls
            if isinstance(call, dict) and call.get("id")
        }
        if ids & tool_call_ids:
            return candidate
    raise GraphMessageCompactionError(
        "graph checkpoint contains ToolMessages without a matching AI tool call"
    )


def _earliest_unresolved_tool_call(
    messages: list[BaseMessage], *, fallback: int
) -> int:
    """Preserve AI calls that still lack a ToolMessage, including pending approvals."""
    resolved = {
        message.tool_call_id
        for message in messages
        if isinstance(message, ToolMessage) and message.tool_call_id
    }
    unresolved: list[int] = []
    for index, message in enumerate(messages):
        if not isinstance(message, AIMessage) or not message.tool_calls:
            continue
        call_ids = {
            str(call.get("id"))
            for call in message.tool_calls
            if isinstance(call, dict) and call.get("id")
        }
        if call_ids - resolved:
            unresolved.append(index)
    return min(unresolved, default=fallback)


def _ensure_message_id(
    message: BaseMessage, compaction_id: str, index: int
) -> BaseMessage:
    if message.id:
        return message
    return message.model_copy(update={"id": f"sage:{compaction_id}:preserved:{index}"})


__all__ = [
    "GraphMessageCompactionError",
    "GraphMessageCompactionPlan",
    "GraphMessageCompactionRequest",
    "build_graph_message_compaction_plan",
    "load_graph_message_compaction_plan",
]
