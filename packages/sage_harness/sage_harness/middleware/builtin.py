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
from sage_harness.state import SageThreadState

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
    escaped = escaped.replace(_BEGIN_INPUT, "[BEGIN USER INPUT]").replace(_END_INPUT, "[END USER INPUT]")
    return f"{_BEGIN_INPUT}\n{escaped}\n{_END_INPUT}"


def _sanitize_last_user_message(messages: list[AnyMessage]) -> list[AnyMessage]:
    sanitized = list(messages)
    for index in range(len(sanitized) - 1, -1, -1):
        message = sanitized[index]
        if not isinstance(message, HumanMessage):
            continue
        if message.name == "summary" or message.additional_kwargs.get("hide_from_ui"):
            continue
        if isinstance(message.content, str):
            sanitized[index] = message.model_copy(update={"content": neutralize_untrusted_text(message.content)})
        elif isinstance(message.content, list):
            content: list[str | dict[str, Any]] = []
            for block in message.content:
                if isinstance(block, str):
                    content.append(neutralize_untrusted_text(block))
                elif isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
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
            "thread_data": {"workspace_path": context.workspace_path},
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


class TokenBudgetMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Persist per-run token usage and stop before another over-budget model call."""

    state_schema = SageThreadState

    def __init__(self, max_tokens: int) -> None:
        super().__init__()
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
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
        return {"budget_run_id": context.run_id, "run_token_usage": 0}

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
        _ = runtime
        used = state.get("run_token_usage", 0)
        if used < self.max_tokens:
            return None
        message = AIMessage(
            content="本轮已达到 token 安全上限，已停止继续调用模型。",
            additional_kwargs={"sage_harness": {"stop_reason": "token_capped"}},
        )
        return {"jump_to": "end", "messages": [message]}

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
        _ = runtime
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], AIMessage):
            return None
        return {"run_token_usage": state.get("run_token_usage", 0) + _message_usage(messages[-1])}

    @override
    async def aafter_model(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> dict[str, object] | None:
        return self.after_model(state, runtime)


def _has_visible_text(message: AIMessage) -> bool:
    if isinstance(message.content, str):
        return bool(message.content.strip())
    return any(
        isinstance(block, dict) and block.get("type") == "text" and bool(str(block.get("text") or "").strip())
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
        if not messages or not isinstance(messages[-1], AIMessage) or not _has_visible_text(messages[-1]):
            raise MissingTerminalResponseError("Agent completed without a visible assistant response")

    @override
    async def aafter_agent(
        self,
        state: SageThreadState,
        runtime: Runtime[HarnessRunContext],
    ) -> None:
        self.after_agent(state, runtime)
