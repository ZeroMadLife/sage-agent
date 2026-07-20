"""Small, application-neutral middleware set for the first harness slice."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, hook_config
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.runtime import Runtime
from langgraph.types import Command

from sage_harness.config import HarnessRunContext
from sage_harness.ports import ToolArtifactPort
from sage_harness.state import SageThreadState, delegation_budget_usage

_BLOCKED_TAGS = frozenset(
    {
        "analysis",
        "instruction",
        "memory",
        "override",
        "prompt",
        "role",
        "system",
        "system-reminder",
        "system_reminder",
        "think",
    }
)
_BLOCKED_TAG_PATTERN = re.compile(
    r"<\s*/?\s*(?:" + "|".join(re.escape(tag) for tag in sorted(_BLOCKED_TAGS)) + r")\b[^>]*>?",
    re.IGNORECASE,
)
_BEGIN_INPUT = "--- BEGIN USER INPUT ---"
_END_INPUT = "--- END USER INPUT ---"
_BEGIN_REMOTE = "--- BEGIN REMOTE TOOL CONTENT ---"
_END_REMOTE = "--- END REMOTE TOOL CONTENT ---"
_MAX_REMOTE_CONTENT_CHARS = 12_000
_LEGACY_TOOL_BLOCK = re.compile(
    r"<tool>\s*\{.*?\}\s*</tool>",
    re.IGNORECASE | re.DOTALL,
)
_LEGACY_FINAL_BLOCK = re.compile(
    r"<final>(.*?)</final>",
    re.IGNORECASE | re.DOTALL,
)


class MissingRunContextError(RuntimeError):
    """Raised when a host starts the graph without a server-owned run context."""


class ProviderCallError(RuntimeError):
    """Safe provider failure that retains classification without leaking secrets."""

    def __init__(self, kind: str, *, retryable: bool) -> None:
        self.kind = kind
        self.retryable = retryable
        super().__init__(f"Provider call failed ({kind})")


class MissingTerminalResponseError(RuntimeError):
    """Raised when a completed graph has no user-visible assistant response."""


def neutralize_untrusted_text(text: str) -> str:
    """Escape reserved tags and wrap a genuine user payload in clear boundaries."""
    if not text.strip():
        return text
    escaped = _BLOCKED_TAG_PATTERN.sub(
        lambda match: match.group(0).replace("<", "&lt;").replace(">", "&gt;"),
        text,
    )
    escaped = escaped.replace(_BEGIN_INPUT, "[BEGIN USER INPUT]").replace(
        _END_INPUT, "[END USER INPUT]"
    )
    return f"{_BEGIN_INPUT}\n{escaped}\n{_END_INPUT}"


def neutralize_remote_content_text(text: str) -> tuple[str, bool]:
    """Bound and neutralize potentially hostile content returned by MCP tools."""
    bounded = text[:_MAX_REMOTE_CONTENT_CHARS]
    truncated = len(text) > len(bounded)
    escaped = _BLOCKED_TAG_PATTERN.sub(
        lambda match: match.group(0).replace("<", "&lt;").replace(">", "&gt;"),
        bounded,
    )
    escaped = (
        escaped.replace(_BEGIN_REMOTE, "[BEGIN REMOTE TOOL CONTENT]")
        .replace(_END_REMOTE, "[END REMOTE TOOL CONTENT]")
        .replace(_BEGIN_INPUT, "[BEGIN USER INPUT]")
        .replace(_END_INPUT, "[END USER INPUT]")
    )
    suffix = "\n[remote content truncated]" if truncated else ""
    return f"{_BEGIN_REMOTE}\n{escaped}{suffix}\n{_END_REMOTE}", truncated


def _sanitize_last_user_message(messages: list[AnyMessage]) -> list[AnyMessage]:
    sanitized = list(messages)
    for index in range(len(sanitized) - 1, -1, -1):
        message = sanitized[index]
        if not isinstance(message, HumanMessage):
            continue
        if message.name == "summary" or message.additional_kwargs.get("hide_from_ui"):
            continue
        if isinstance(message.content, str):
            sanitized[index] = message.model_copy(
                update={"content": neutralize_untrusted_text(message.content)}
            )
        elif isinstance(message.content, list):
            content: list[str | dict[str, Any]] = []
            for block in message.content:
                if isinstance(block, str):
                    content.append(neutralize_untrusted_text(block))
                elif (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                ):
                    content.append({**block, "text": neutralize_untrusted_text(block["text"])})
                else:
                    content.append(block)
            sanitized[index] = message.model_copy(update={"content": content})
        return sanitized
    return sanitized


class InputSanitizationMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Temporarily sanitize the latest genuine user message before each model call."""

    state_schema = SageThreadState

    @override
    def wrap_model_call(
        self,
        request: ModelRequest[HarnessRunContext],
        handler: Callable[[ModelRequest[HarnessRunContext]], ModelCallResult],
    ) -> ModelCallResult:
        sanitized = _sanitize_last_user_message(request.messages)
        return handler(request.override(messages=sanitized))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[HarnessRunContext],
        handler: Callable[[ModelRequest[HarnessRunContext]], Awaitable[ModelCallResult]],
    ) -> ModelCallResult:
        sanitized = _sanitize_last_user_message(request.messages)
        return await handler(request.override(messages=sanitized))


class ThreadContextMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Project server-owned invocation context into checkpoint-safe references."""

    state_schema = SageThreadState

    @staticmethod
    def _project(runtime: Runtime[HarnessRunContext]) -> dict[str, object]:
        context = runtime.context
        if not isinstance(context, HarnessRunContext):
            raise MissingRunContextError("HarnessRunContext is required")
        surface_metadata = context.metadata.get("surface_context")
        if not isinstance(surface_metadata, Mapping):
            surface_metadata = {}
        return {
            "thread_data": {
                "owner_id": context.owner_id,
                "workspace_id": context.workspace_id,
                "thread_id": context.thread_id,
                "workspace_path": context.workspace_path,
            },
            "surface_context": {
                **{str(key): value for key, value in surface_metadata.items()},
                "thread_id": context.thread_id,
                "run_id": context.run_id,
                "workspace_id": context.workspace_id,
                "surface": context.surface,
            },
        }

    @override
    def before_agent(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object]:
        _ = state
        return self._project(runtime)

    @override
    async def abefore_agent(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object]:
        return self.before_agent(state, runtime)


def _classify_provider_error(exc: Exception) -> tuple[str, bool]:
    text = str(exc).lower()
    name = exc.__class__.__name__.lower()
    if any(token in text for token in ("api key", "unauthorized", "forbidden", "authentication")):
        return "auth", False
    if any(token in text for token in ("quota", "billing", "credit", "余额")):
        return "quota", False
    if "rate" in text and "limit" in text:
        return "rate_limit", True
    if any(token in name for token in ("timeout", "connection", "protocol")):
        return "transient", True
    return "provider_error", False


class ProviderErrorMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Normalize provider failures and preserve failed-run semantics."""

    state_schema = SageThreadState

    @override
    def wrap_model_call(
        self,
        request: ModelRequest[HarnessRunContext],
        handler: Callable[[ModelRequest[HarnessRunContext]], ModelCallResult],
    ) -> ModelCallResult:
        try:
            return handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            kind, retryable = _classify_provider_error(exc)
            raise ProviderCallError(kind, retryable=retryable) from exc

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[HarnessRunContext],
        handler: Callable[[ModelRequest[HarnessRunContext]], Awaitable[ModelCallResult]],
    ) -> ModelCallResult:
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            kind, retryable = _classify_provider_error(exc)
            raise ProviderCallError(kind, retryable=retryable) from exc


class ToolErrorMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Convert tool exceptions into bounded error messages so the loop can recover."""

    state_schema = SageThreadState

    @staticmethod
    def _error_result(request: ToolCallRequest, exc: Exception) -> ToolMessage:
        tool_name = str(request.tool_call.get("name") or "unknown_tool")
        tool_call_id = str(request.tool_call.get("id") or "missing_tool_call_id")
        return ToolMessage(
            content=(
                f"Tool '{tool_name}' failed with {exc.__class__.__name__}. "
                "Continue with available context or choose an alternative tool."
            ),
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
            additional_kwargs={"sage_harness": {"error_type": exc.__class__.__name__}},
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        try:
            return handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            return self._error_result(request, exc)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            return self._error_result(request, exc)


class RemoteContentSanitizationMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Neutralize only tools explicitly tagged as remote-content sources."""

    state_schema = SageThreadState

    @staticmethod
    def _is_remote(request: ToolCallRequest) -> bool:
        metadata = request.tool.metadata if request.tool is not None else None
        return isinstance(metadata, Mapping) and metadata.get("remote_content") is True

    @staticmethod
    def _sanitize(result: ToolMessage | Command[Any]) -> ToolMessage | Command[Any]:
        if not isinstance(result, ToolMessage):
            return result
        truncated = False
        content = result.content
        if isinstance(content, str):
            sanitized, truncated = neutralize_remote_content_text(content)
            content = sanitized
        elif isinstance(content, list):
            sanitized_blocks: list[str | dict[str, Any]] = []
            for block in content:
                if isinstance(block, str):
                    sanitized, block_truncated = neutralize_remote_content_text(block)
                    sanitized_blocks.append(sanitized)
                    truncated = truncated or block_truncated
                elif (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                ):
                    sanitized, block_truncated = neutralize_remote_content_text(block["text"])
                    sanitized_blocks.append({**block, "text": sanitized})
                    truncated = truncated or block_truncated
                else:
                    sanitized_blocks.append(block)
            content = sanitized_blocks
        harness_meta = result.additional_kwargs.get("sage_harness")
        metadata = dict(harness_meta) if isinstance(harness_meta, Mapping) else {}
        metadata.update({"remote_content": True, "truncated": truncated})
        return result.model_copy(
            update={
                "content": content,
                "additional_kwargs": {
                    **result.additional_kwargs,
                    "sage_harness": metadata,
                },
            }
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        result = handler(request)
        return self._sanitize(result) if self._is_remote(request) else result

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        result = await handler(request)
        return self._sanitize(result) if self._is_remote(request) else result


class ToolResultArtifactMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Move large tool text out of graph state and retain an opaque reference."""

    state_schema = SageThreadState

    def __init__(self, store: ToolArtifactPort, *, minimum_bytes: int = 16 * 1024) -> None:
        if minimum_bytes < 1:
            raise ValueError("minimum_bytes must be positive")
        self._store = store
        self._minimum_bytes = minimum_bytes

    def _archive(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[Any],
    ) -> ToolMessage | Command[Any]:
        if not isinstance(result, ToolMessage) or result.name == "knowledge_search":
            return result
        if result.artifact is not None or not isinstance(result.content, str):
            return result
        if len(result.content.encode("utf-8")) < self._minimum_bytes:
            return result
        call_id = str(request.tool_call.get("id") or result.tool_call_id or "").strip()
        if not call_id:
            return result
        receipt = self._store.archive(call_id, result.content)
        return result.model_copy(
            update={
                "content": receipt.preview,
                "artifact": {
                    "artifact_ref": receipt.artifact_ref,
                    "original_chars": receipt.original_chars,
                    "truncated": receipt.truncated,
                },
            }
        )

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        return self._archive(request, handler(request))

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        return self._archive(request, await handler(request))


def _message_usage(message: AIMessage) -> int:
    usage = message.usage_metadata
    if usage is None:
        return 0
    total = usage.get("total_tokens")
    if isinstance(total, int):
        return max(total, 0)
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    return max(int(input_tokens), 0) + max(int(output_tokens), 0)


_BUDGET_NOTICES = {
    "model_call_capped": "本轮已达到模型调用安全上限，已停止继续调用工具。",
    "token_capped": "本轮已达到 token 安全上限，已停止继续调用工具。",
    "tool_call_capped": "本轮已达到工具调用安全上限，已停止继续调用工具。",
}


def _counter(state: Mapping[str, object], key: str) -> int:
    value = state.get(key, 0)
    return max(value, 0) if isinstance(value, int) else 0


def _child_budget_usage(state: SageThreadState, run_id: str) -> tuple[int, int, int]:
    """Aggregate only children owned by the active parent run.

    Pending and running children consume their full token reservation so a
    concurrent batch cannot overbook the parent budget. Terminal children use
    their measured counters instead.
    """
    tokens = 0
    model_calls = 0
    tool_calls = 0
    for entry in state.get("delegations", []) or []:
        if entry.get("run_id") != run_id:
            continue
        entry_tokens, entry_models, entry_tools = delegation_budget_usage(entry)
        tokens += entry_tokens
        model_calls += entry_models
        tool_calls += entry_tools
    return tokens, model_calls, tool_calls


def _append_budget_notice(content: object, notice: str) -> object:
    if isinstance(content, str):
        return f"{content}\n\n{notice}" if content else notice
    if isinstance(content, list):
        return [*content, {"type": "text", "text": f"\n\n{notice}"}]
    return notice


def _public_budget_content(content: object) -> object:
    """Remove legacy tool protocol from a budget-stopped public response."""
    if isinstance(content, str):
        final_blocks = _LEGACY_FINAL_BLOCK.findall(content)
        cleaned = final_blocks[-1] if final_blocks else _LEGACY_TOOL_BLOCK.sub("", content)
        return cleaned.replace("<final>", "").replace("</final>", "").strip()
    if isinstance(content, list):
        projected: list[object] = []
        for block in content:
            if isinstance(block, str):
                cleaned = _public_budget_content(block)
                if cleaned:
                    projected.append(cleaned)
            elif (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                cleaned = _public_budget_content(block["text"])
                if cleaned:
                    projected.append({**block, "text": cleaned})
            else:
                projected.append(block)
        return projected
    return ""


def _budget_stop_message(
    message: AIMessage,
    *,
    reason: str,
    used: int,
    limit: int,
) -> AIMessage:
    notice = _BUDGET_NOTICES[reason]
    additional_kwargs = dict(message.additional_kwargs)
    additional_kwargs.pop("tool_calls", None)
    additional_kwargs.pop("function_call", None)
    harness_meta = additional_kwargs.get("sage_harness")
    public_meta = dict(harness_meta) if isinstance(harness_meta, Mapping) else {}
    public_meta.update(
        {
            "stop_reason": reason,
            "used": used,
            "limit": limit,
            "notice": notice,
        }
    )
    additional_kwargs["sage_harness"] = public_meta
    response_metadata = dict(message.response_metadata)
    if response_metadata.get("finish_reason") == "tool_calls":
        response_metadata["finish_reason"] = "stop"
    return message.model_copy(
        update={
            "content": _append_budget_notice(_public_budget_content(message.content), notice),
            "tool_calls": [],
            "invalid_tool_calls": [],
            "additional_kwargs": additional_kwargs,
            "response_metadata": response_metadata,
        }
    )


class RunBudgetMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Enforce model, tool and token limits that survive same-run resumes."""

    state_schema = SageThreadState

    def __init__(
        self,
        *,
        max_model_calls: int,
        max_tool_calls: int,
        max_tokens: int,
    ) -> None:
        super().__init__()
        if max_model_calls < 1:
            raise ValueError("max_model_calls must be positive")
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be positive")
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self.max_model_calls = max_model_calls
        self.max_tool_calls = max_tool_calls
        self.max_tokens = max_tokens

    @override
    def before_agent(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        context = runtime.context
        if not isinstance(context, HarnessRunContext):
            raise MissingRunContextError("HarnessRunContext is required")
        if state.get("budget_run_id") == context.run_id:
            return None
        return {
            "budget_run_id": context.run_id,
            "run_token_usage": 0,
            "run_model_calls": 0,
            "run_tool_calls": 0,
            "run_child_token_usage": 0,
            "run_child_model_calls": 0,
            "run_child_tool_calls": 0,
            "run_token_limit": self.max_tokens,
            "run_model_call_limit": self.max_model_calls,
            "run_tool_call_limit": self.max_tool_calls,
        }

    @override
    async def abefore_agent(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        return self.before_agent(state, runtime)

    @hook_config(can_jump_to=["end"])
    @override
    def before_model(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        child_tokens, child_models, child_tools = _child_budget_usage(
            state,
            runtime.context.run_id,
        )
        used_tokens = _counter(state, "run_token_usage") + child_tokens
        model_calls = _counter(state, "run_model_calls") + child_models
        child_update: dict[str, object] = {
            "run_child_token_usage": child_tokens,
            "run_child_model_calls": child_models,
            "run_child_tool_calls": child_tools,
        }
        if used_tokens < self.max_tokens and model_calls < self.max_model_calls:
            return child_update
        reason = "token_capped" if used_tokens >= self.max_tokens else "model_call_capped"
        used = used_tokens if reason == "token_capped" else model_calls
        limit = self.max_tokens if reason == "token_capped" else self.max_model_calls
        message = _budget_stop_message(
            AIMessage(content=""),
            reason=reason,
            used=used,
            limit=limit,
        )
        return {**child_update, "jump_to": "end", "messages": [message]}

    @hook_config(can_jump_to=["end"])
    @override
    async def abefore_model(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        return self.before_model(state, runtime)

    @override
    def after_model(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        context = runtime.context
        if not isinstance(context, HarnessRunContext):
            raise MissingRunContextError("HarnessRunContext is required")
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        message = messages[-1]
        child_tokens, child_models, child_tools = _child_budget_usage(
            state,
            context.run_id,
        )
        parent_tokens = _counter(state, "run_token_usage") + _message_usage(message)
        parent_model_calls = _counter(state, "run_model_calls") + 1
        used_tokens = parent_tokens + child_tokens
        model_calls = parent_model_calls + child_models
        prior_tool_calls = _counter(state, "run_tool_calls")
        proposed_tool_calls = len(message.tool_calls)
        proposed_parent_tools = prior_tool_calls + proposed_tool_calls
        proposed_total_tools = proposed_parent_tools + child_tools
        update: dict[str, object] = {
            "run_token_usage": parent_tokens,
            "run_model_calls": parent_model_calls,
            "run_tool_calls": prior_tool_calls,
            "run_child_token_usage": child_tokens,
            "run_child_model_calls": child_models,
            "run_child_tool_calls": child_tools,
        }

        stop_reason: str | None = None
        used = 0
        limit = 0
        if used_tokens >= self.max_tokens:
            stop_reason, used, limit = "token_capped", used_tokens, self.max_tokens
        elif proposed_tool_calls and model_calls >= self.max_model_calls:
            stop_reason, used, limit = (
                "model_call_capped",
                model_calls,
                self.max_model_calls,
            )
        elif proposed_tool_calls and proposed_total_tools > self.max_tool_calls:
            stop_reason, used, limit = (
                "tool_call_capped",
                proposed_total_tools,
                self.max_tool_calls,
            )

        if stop_reason is not None:
            update["messages"] = [
                _budget_stop_message(
                    message,
                    reason=stop_reason,
                    used=used,
                    limit=limit,
                )
            ]
            return update

        update["run_tool_calls"] = proposed_parent_tools
        return update

    @override
    async def aafter_model(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        return self.after_model(state, runtime)


class TokenBudgetMiddleware(RunBudgetMiddleware):
    """Backward-compatible token-only constructor for host extensions."""

    def __init__(self, max_tokens: int) -> None:
        super().__init__(
            max_model_calls=2**31 - 1,
            max_tool_calls=2**31 - 1,
            max_tokens=max_tokens,
        )


def _has_visible_text(message: AIMessage) -> bool:
    if isinstance(message.content, str):
        return bool(message.content.strip())
    return any(
        isinstance(block, dict)
        and block.get("type") == "text"
        and bool(str(block.get("text") or "").strip())
        for block in message.content
    )


class TerminalResponseMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Fail a completed graph that has no user-visible assistant response."""

    state_schema = SageThreadState

    @override
    def after_agent(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> None:
        _ = runtime
        messages = state.get("messages", [])
        if (
            not messages
            or not isinstance(messages[-1], AIMessage)
            or not _has_visible_text(messages[-1])
        ):
            raise MissingTerminalResponseError(
                "Agent completed without a visible assistant response"
            )

    @override
    async def aafter_agent(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> None:
        self.after_agent(state, runtime)
