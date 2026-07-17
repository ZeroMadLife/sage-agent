"""Async generator engine for one coding-agent turn."""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from copy import deepcopy
from typing import Any, Protocol

from core.coding.context import (
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    ContextManager,
    WorkspaceContext,
    now,
)
from core.coding.engine.events import (
    CancelledEvent,
    ErrorEvent,
    FinalEvent,
    ModelParsedEvent,
    ModelRequestedEvent,
    RetryEvent,
    StepLimitEvent,
    TextDeltaEvent,
    ToolResultEvent,
    event_to_dict,
)
from core.coding.engine.helpers import build_tool_descriptions, step_limit_summary
from core.coding.engine.model_output import parse
from core.coding.tool_executor.approval import ApprovalManager
from core.coding.tool_executor.executor import ToolExecutor
from core.coding.tool_executor.permissions import PermissionChecker
from core.coding.tool_executor.policy import ToolPolicyChecker
from core.coding.tools.base import RegisteredTool
from core.coding.tools.registry import (
    ToolArgumentValidationError,
    get_active_tools,
    validate_tool_preflight,
)
from core.coding.usage_store import UsageSample, normalize_usage

logger = logging.getLogger(__name__)


class ApiClient(Protocol):
    """Minimal model contract used by the coding engine."""

    async def complete(self, prompt: str) -> str:
        """Return raw model text."""
        ...


ModelClient = ApiClient


class PreparedContextLike(Protocol):
    """Read-only model-request projection returned by a runtime safe-point hook."""

    @property
    def events(self) -> tuple[Any, ...]: ...

    @property
    def allow_model_request(self) -> bool: ...

    @property
    def projected_history(self) -> list[dict[str, Any]]: ...


class Engine:
    """Turn control loop: model -> parse -> tool -> final."""

    MAX_PROTOCOL_RETRIES = 2
    MAX_TOOL_ARGUMENT_RETRIES = 2

    def __init__(
        self,
        model: Any,
        workspace: WorkspaceContext,
        tools: dict[str, RegisteredTool],
        context_manager: ContextManager,
        permission_checker: PermissionChecker,
        policy_checker: ToolPolicyChecker,
        session_id: str = "",
        approval_manager: ApprovalManager | None = None,
        should_stop: Callable[[], bool] | None = None,
        history: list[dict[str, Any]] | None = None,
        activated_tools: set[str] | None = None,
        tool_executor: ToolExecutor | None = None,
        run_id: str = "",
        workspace_reminders: list[str] | None = None,
        max_steps: int = 50,
        append_user: bool = True,
        current_message_id: str | None = None,
        append_history: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        before_model_request: Callable[
            [list[dict[str, Any]]], PreparedContextLike
        ]
        | None = None,
        model_usage_sink: Callable[[int, UsageSample], None] | None = None,
    ) -> None:
        self.model = model
        self.workspace = workspace
        self.tools = tools
        self.context_manager = context_manager
        self.permission_checker = permission_checker
        self.policy_checker = policy_checker
        self.session_id = session_id
        self.approval_manager = approval_manager
        self.should_stop = should_stop or (lambda: False)
        self.history = history if history is not None else []
        self.activated_tools = activated_tools if activated_tools is not None else set()
        self.tool_executor = tool_executor
        self.run_id = run_id
        self.workspace_reminders = workspace_reminders or []
        self.max_steps = max_steps
        self.append_user = append_user
        self.current_message_id = current_message_id
        self.append_history = append_history
        self.before_model_request = before_model_request
        self.model_usage_sink = model_usage_sink

    async def run_turn(
        self,
        user_message: str,
        skill_prompt: str | None = None,
        memory_block: str | None = None,
        surface_context: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Run one coding turn and yield streamable events.

        ``skill_prompt`` is an expanded skill instruction injected into the LLM
        prompt for this turn only; it is never written to ``self.history``.

        ``memory_block`` is a rendered memory context (working + durable memory)
        injected into the LLM prompt for this turn only; it is never written to
        ``self.history``.

        ``surface_context`` is the server-validated run binding. It is injected
        as data for this turn and is never written to ``self.history``.
        """
        if self.append_user:
            self._append_history({"role": "user", "content": user_message, "created_at": now()})
        tool_steps = 0
        attempts = 0
        protocol_retries = 0
        tool_argument_retries = 0
        protocol_correction = ""

        last_tool_signature: tuple[str, str] = ("", "")
        repeat_count = 0
        MAX_REPEAT = 3

        while tool_steps < self.max_steps and attempts < self.max_steps + 2:
            if self.should_stop():
                yield self._cancelled_event()
                return
            prompt_history = self._history_without_current()
            if self.before_model_request is not None:
                prepared = self.before_model_request(deepcopy(self.history))
                for context_event in prepared.events:
                    yield event_to_dict(context_event)
                if not prepared.allow_model_request:
                    message = "context emergency: model request blocked"
                    self._append_history(
                        {
                            "role": "assistant",
                            "content": message,
                            "is_error": True,
                            "created_at": now(),
                        }
                    )
                    yield event_to_dict(
                        ErrorEvent(
                            run_id=self.run_id,
                            message=message,
                        )
                    )
                    yield self._cancelled_event()
                    return
                prompt_history = prepared.projected_history
            prompt_history = self._history_for_prompt(prompt_history)
            attempts += 1
            prompt, metadata = self.context_manager.build(
                user_message=user_message,
                history=prompt_history,
                tools=self._tool_descriptions(),
                workspace_reminders=self.workspace_reminders,
                deferred_tools=self._deferred_tool_names(),
                skill_prompt=skill_prompt,
                surface_context=surface_context,
                memory_block=memory_block,
                include_current_request=self.current_message_id is None,
            )
            if protocol_correction:
                prompt = f"{prompt}\n\n<protocol-correction>\n{protocol_correction}\n</protocol-correction>"
            yield event_to_dict(
                ModelRequestedEvent(
                    run_id=self.run_id,
                    attempts=attempts,
                    tool_steps=tool_steps,
                    prompt_chars=metadata["prompt_chars"],
                )
            )

            raw = ""
            streamed_final_chars = 0
            astream = getattr(self.model, "astream", None)
            if callable(astream):
                messages = self._build_ainvoke_messages(prompt)
                latest_usage: UsageSample | None = None
                async for chunk in astream(messages):
                    sample = normalize_usage(chunk)
                    if sample is not None:
                        latest_usage = sample
                    delta = self._text_content(getattr(chunk, "content", ""))
                    if delta:
                        raw += delta
                        visible_delta, streamed_final_chars = self._visible_final_delta(
                            raw, streamed_final_chars
                        )
                        if visible_delta:
                            yield event_to_dict(
                                TextDeltaEvent(run_id=self.run_id, delta=visible_delta)
                            )
                if latest_usage is not None:
                    self._record_usage(attempts, latest_usage)
            else:
                raw = await self._call_model(prompt, attempts)
            if self.should_stop():
                yield self._cancelled_event()
                return
            kind, payload = parse(raw)
            yield event_to_dict(ModelParsedEvent(run_id=self.run_id, kind=kind))

            if kind in {"tool", "tools"}:
                tool_payloads = [payload] if kind == "tool" else list(payload)
                tool_correction = ""
                try:
                    for tool_payload in tool_payloads:
                        self._validate_tool_payload(tool_payload)
                except ToolArgumentValidationError as exc:
                    if tool_argument_retries >= self.MAX_TOOL_ARGUMENT_RETRIES:
                        content = (
                            f"工具 {exc.tool_name} 多次缺少或使用了无效参数，"
                            "已停止本次运行。请补充明确的工作区相对路径后重试。"
                        )
                        self._append_history(
                            {"role": "assistant", "content": content, "created_at": now()}
                        )
                        yield event_to_dict(FinalEvent(run_id=self.run_id, content=content))
                        return
                    tool_argument_retries += 1
                    tool_correction = self._tool_argument_correction(exc)
                    yield event_to_dict(RetryEvent(run_id=self.run_id, content=tool_correction))

                if tool_correction:
                    protocol_correction = tool_correction
                    continue

                recovered_tool_arguments = tool_argument_retries > 0
                for tool_payload in tool_payloads:
                    # Detect only repeated *identical* calls. A coding task can
                    # legitimately refine the same file across several writes, so
                    # path alone is not enough to prove a loop.
                    tool_name = str(tool_payload.get("name", ""))
                    tool_args = tool_payload.get("args", {})
                    serialized_args = json.dumps(
                        tool_args if isinstance(tool_args, dict) else {},
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                    sig = (tool_name, serialized_args)
                    if sig == last_tool_signature:
                        repeat_count += 1
                    else:
                        repeat_count = 0
                        last_tool_signature = sig
                    if repeat_count >= MAX_REPEAT:
                        notice = f"检测到工具 {sig[0]} 连续重复调用 {MAX_REPEAT} 次,已停止以避免无限循环。"
                        self._append_history(
                            {"role": "assistant", "content": notice, "created_at": now()}
                        )
                        yield event_to_dict(FinalEvent(run_id=self.run_id, content=notice))
                        return
                    if tool_steps >= self.max_steps:
                        break
                    if self.should_stop():
                        yield self._cancelled_event()
                        return
                    async for event in self._execute_tool_payload(tool_payload):
                        yield event
                        if event["type"] == "cancelled":
                            return
                    tool_steps += 1
                protocol_correction = (
                    "This is the same turn, and the malformed tool step has already been "
                    "corrected and executed successfully. Continue from the latest tool "
                    "result without restarting earlier steps. If that result answers the "
                    "request, return <final> now; never repeat an intentionally malformed call."
                    if recovered_tool_arguments
                    else ""
                )
                continue

            if kind == "retry":
                if self._can_accept_plain_final(raw, tool_steps, protocol_retries):
                    final = raw.strip()
                    self._append_history(
                        {"role": "assistant", "content": final, "created_at": now()}
                    )
                    yield event_to_dict(FinalEvent(run_id=self.run_id, content=final))
                    return
                notice = str(payload)
                if protocol_retries >= self.MAX_PROTOCOL_RETRIES:
                    content = "模型连续返回了无法执行的操作格式，已停止本次运行。请重试，或换用更兼容的模型。"
                    self._append_history(
                        {"role": "assistant", "content": content, "created_at": now()}
                    )
                    yield event_to_dict(FinalEvent(run_id=self.run_id, content=content))
                    return
                protocol_retries += 1
                protocol_correction = notice
                yield event_to_dict(RetryEvent(run_id=self.run_id, content=notice))
                continue

            final = str(payload).strip()
            self._append_history({"role": "assistant", "content": final, "created_at": now()})
            yield event_to_dict(FinalEvent(run_id=self.run_id, content=final))
            return

        content = self._step_limit_summary(user_message, tool_steps)
        self._append_history({"role": "assistant", "content": content, "created_at": now()})
        yield event_to_dict(StepLimitEvent(run_id=self.run_id, content=content))

    @staticmethod
    def _can_accept_plain_final(raw: str, tool_steps: int, protocol_retries: int) -> bool:
        """Accept a guarded plain-text final after a successful tool turn.

        Some OpenAI-compatible providers drop only the final wrapper after a
        tool result.  The first malformed response remains a retry so we never
        mistake planning prose for an action.  A second plain response is safe
        to surface only after at least one tool completed and one correction
        has already been supplied.
        """
        text = raw.strip()
        return (
            bool(text)
            and tool_steps > 0
            and protocol_retries > 0
            and "<tool" not in text.lower()
            and "<final" not in text.lower()
        )

    async def _execute_tool_payload(self, payload: Any) -> AsyncIterator[dict[str, Any]]:
        executor = self.tool_executor or ToolExecutor(
            tools=self.tools,
            workspace=self.workspace,
            permission_checker=self.permission_checker,
            policy_checker=self.policy_checker,
            approval_manager=self.approval_manager,
            session_id=self.session_id,
            should_stop=self.should_stop,
            run_id=self.run_id,
        )
        async for event in executor.execute(payload):
            if isinstance(event, ToolResultEvent):
                self._append_tool_history(event)
            if isinstance(event, CancelledEvent):
                self._append_cancelled_history(event.content)
            yield event_to_dict(event)

    def _validate_tool_payload(self, payload: Any) -> None:
        """Reject malformed tool arguments before any call or approval is emitted."""
        if not isinstance(payload, dict):
            return
        name = str(payload.get("name", ""))
        args = payload.get("args", {})
        if name not in self.tools or not isinstance(args, dict):
            return
        validate_tool_preflight(self.workspace, name, args)

    @staticmethod
    def _tool_argument_correction(error: ToolArgumentValidationError) -> str:
        schema = json.dumps(error.schema, ensure_ascii=False, sort_keys=True)
        return (
            f"Tool {error.tool_name} arguments were not executed because {error}. "
            f"Its required schema is {schema}. Return one corrected <tool> call with "
            "workspace-relative paths only; never invent a path, use an absolute path, "
            "or use '..' traversal."
        )

    def _cancelled_event(self) -> dict[str, Any]:
        content = "已停止当前运行。"
        self._append_cancelled_history(content)
        return event_to_dict(CancelledEvent(run_id=self.run_id, content=content))

    def _append_tool_history(self, event: ToolResultEvent) -> None:
        self._append_history(
            {
                "role": "tool",
                "name": event.tool,
                "args": event.args,
                "content": event.content,
                "is_error": event.is_error,
                "policy_reason": event.policy_reason or "",
                "security_event_type": event.security_event_type or "",
                "created_at": now(),
            }
        )

    def _append_cancelled_history(self, content: str) -> None:
        if (
            self.history
            and self.history[-1].get("role") == "assistant"
            and self.history[-1].get("content") == content
        ):
            return
        self._append_history({"role": "assistant", "content": content, "created_at": now()})

    def _append_history(self, item: dict[str, Any]) -> dict[str, Any]:
        if self.append_history is not None:
            return self.append_history(item)
        self.history.append(item)
        return item

    def _history_without_current(self) -> list[dict[str, Any]]:
        history = deepcopy(self.history)
        if self.current_message_id is None:
            return history
        indexes = [
            index
            for index, item in enumerate(history)
            if item.get("message_id") == self.current_message_id
        ]
        if len(indexes) > 1:
            raise ValueError("current_message_id is not unique in history")
        if indexes:
            history.pop(indexes[0])
        return history

    def _history_for_prompt(
        self, projected_history: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        history = deepcopy(projected_history)
        if self.current_message_id is None:
            return history
        current = [
            item
            for item in self.history
            if item.get("message_id") == self.current_message_id
        ]
        if len(current) != 1:
            raise ValueError("current_message_id must identify exactly one history item")
        history = [
            item
            for item in history
            if item.get("message_id") != self.current_message_id
        ]
        current_item = deepcopy(current[0])
        current_sequence = current_item.get("sequence")
        insert_at = len(history)
        if isinstance(current_sequence, int) and not isinstance(current_sequence, bool):
            for index, item in enumerate(history):
                sequence = item.get("sequence")
                if (
                    isinstance(sequence, int)
                    and not isinstance(sequence, bool)
                    and sequence > current_sequence
                ):
                    insert_at = index
                    break
        history.insert(insert_at, current_item)
        return history

    async def _call_model(self, prompt: str, attempt: int) -> str:
        complete = getattr(self.model, "complete", None)
        if callable(complete):
            result = complete(prompt)
            if inspect.isawaitable(result):
                value = await result
            else:
                value = result
            sample = normalize_usage(value)
            if sample is not None:
                self._record_usage(attempt, sample)
            return self._text_content(getattr(value, "content", value))

        ainvoke = getattr(self.model, "ainvoke", None)
        if callable(ainvoke):
            messages = self._build_ainvoke_messages(prompt)
            response = await ainvoke(messages)
            sample = normalize_usage(response)
            if sample is not None:
                self._record_usage(attempt, sample)
            return self._text_content(getattr(response, "content", response))
        raise TypeError("model must provide complete(prompt) or ainvoke(messages)")

    def _record_usage(self, attempt: int, usage: UsageSample) -> None:
        if self.model_usage_sink is None:
            return
        try:
            self.model_usage_sink(attempt, usage)
        except Exception:
            logger.warning("Unable to persist coding model usage", exc_info=True)

    @staticmethod
    def _text_content(content: object) -> str:
        """Return public text blocks while excluding Provider thinking blocks."""
        if isinstance(content, str):
            return content
        if isinstance(content, Mapping):
            return Engine._text_block(content)
        if not isinstance(content, list):
            return str(content) if content is not None else ""
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if not isinstance(block, Mapping):
                continue
            text = Engine._text_block(block)
            if text:
                parts.append(text)
        return "".join(parts)

    @staticmethod
    def _text_block(block: Mapping[object, object]) -> str:
        block_type = str(block.get("type", "text"))
        text = block.get("text")
        if block_type in {"text", "text_delta", "output_text"} and isinstance(text, str):
            return text
        return ""

    @staticmethod
    def _build_ainvoke_messages(prompt: str) -> list[dict[str, str]]:
        """Split the assembled prompt into a system + user message pair.

        The ``ContextManager`` separates the stable system prompt (identity +
        tool list) from the volatile session context (project context +
        transcript + current request) with the ``SYSTEM_PROMPT_DYNAMIC_BOUNDARY``
        marker. The ainvoke interface supports distinct roles, so we send the
        pre-boundary text as ``role: "system"`` and everything after it as
        ``role: "user"``. When the boundary is absent we fall back to sending
        the whole prompt as a single user message to preserve prior behavior.
        """
        boundary_index = prompt.find(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
        if boundary_index == -1:
            return [{"role": "user", "content": prompt}]
        system_content = prompt[:boundary_index].strip()
        marker_end = boundary_index + len(SYSTEM_PROMPT_DYNAMIC_BOUNDARY)
        user_content = prompt[marker_end:].strip()
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]

    @staticmethod
    def _visible_final_delta(raw: str, emitted_chars: int) -> tuple[str, int]:
        """Expose only user-facing ``<final>`` text during a streamed response.

        The XML tool protocol shares the model token stream with the final answer.
        Forwarding raw chunks would briefly render tool JSON in the chat before the
        parser identifies it. Keep the protocol private and stream only final text,
        with a small closing-tag lookbehind so ``</final>`` never leaks into prose.
        """
        open_tag = "<final>"
        close_tag = "</final>"
        start = raw.find(open_tag)
        if start < 0:
            return "", emitted_chars
        tool_start = raw.find("<tool")
        if 0 <= tool_start < start:
            return "", emitted_chars

        content_start = start + len(open_tag)
        close_at = raw.find(close_tag, content_start)
        if close_at >= 0:
            content_end = close_at
        else:
            content_end = max(content_start, len(raw) - len(close_tag) + 1)

        visible = raw[content_start:content_end]
        if len(visible) <= emitted_chars:
            return "", emitted_chars
        return visible[emitted_chars:], len(visible)

    def _tool_descriptions(self) -> list[str]:
        active_tools = get_active_tools(self.tools, self.activated_tools)
        return build_tool_descriptions(active_tools)

    def _deferred_tool_names(self) -> list[str]:
        return sorted(
            name
            for name, tool in self.tools.items()
            if tool.deferred and name not in self.activated_tools
        )

    @staticmethod
    def _step_limit_summary(user_message: str, tool_steps: int) -> str:
        return step_limit_summary(user_message, tool_steps)
