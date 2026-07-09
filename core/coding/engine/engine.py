"""Async generator engine for one coding-agent turn."""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from core.coding.context import (
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    ContextManager,
    WorkspaceContext,
    now,
)
from core.coding.engine.events import (
    CancelledEvent,
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
from core.coding.tools.registry import get_active_tools


class ApiClient(Protocol):
    """Minimal model contract used by the coding engine."""

    async def complete(self, prompt: str) -> str:
        """Return raw model text."""
        ...


ModelClient = ApiClient


class Engine:
    """Turn control loop: model -> parse -> tool -> final."""

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

    async def run_turn(
        self, user_message: str, skill_prompt: str | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Run one coding turn and yield streamable events.

        ``skill_prompt`` is an expanded skill instruction injected into the LLM
        prompt for this turn only; it is never written to ``self.history``.
        """
        self.history.append({"role": "user", "content": user_message, "created_at": now()})
        tool_steps = 0
        attempts = 0

        last_tool_signature: tuple[str, str] = ("", "")
        repeat_count = 0
        MAX_REPEAT = 3

        while tool_steps < self.max_steps and attempts < self.max_steps + 2:
            if self.should_stop():
                yield self._cancelled_event()
                return
            attempts += 1
            prompt, metadata = self.context_manager.build(
                user_message=user_message,
                history=self.history,
                tools=self._tool_descriptions(),
                workspace_reminders=self.workspace_reminders,
                deferred_tools=self._deferred_tool_names(),
                skill_prompt=skill_prompt,
            )
            yield event_to_dict(
                ModelRequestedEvent(
                    run_id=self.run_id,
                    attempts=attempts,
                    tool_steps=tool_steps,
                    prompt_chars=metadata["prompt_chars"],
                )
            )

            raw = ""
            astream = getattr(self.model, "astream", None)
            if callable(astream):
                messages = self._build_ainvoke_messages(prompt)
                async for chunk in astream(messages):
                    delta = getattr(chunk, "content", "")
                    if isinstance(delta, list):
                        delta = "".join(
                            block.get("text", "") if isinstance(block, dict) else str(block)
                            for block in delta
                        )
                    if delta:
                        raw += str(delta)
                        yield event_to_dict(
                            TextDeltaEvent(run_id=self.run_id, delta=str(delta))
                        )
            else:
                raw = await self._call_model(prompt)
            if self.should_stop():
                yield self._cancelled_event()
                return
            kind, payload = parse(raw)
            yield event_to_dict(ModelParsedEvent(run_id=self.run_id, kind=kind))

            if kind in {"tool", "tools"}:
                tool_payloads = [payload] if kind == "tool" else list(payload)
                for tool_payload in tool_payloads:
                    # Detect repeated identical tool calls to prevent infinite loops
                    sig = (
                        str(tool_payload.get("name", "")),
                        json.dumps(tool_payload.get("args", {}), sort_keys=True),
                    )
                    if sig == last_tool_signature:
                        repeat_count += 1
                    else:
                        repeat_count = 0
                        last_tool_signature = sig
                    if repeat_count >= MAX_REPEAT:
                        notice = (
                            f"检测到工具 {sig[0]} 连续重复调用 {MAX_REPEAT} 次,已停止以避免无限循环。"
                        )
                        self.history.append(
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
                continue

            if kind == "retry":
                notice = str(payload)
                self.history.append({"role": "assistant", "content": notice, "created_at": now()})
                yield event_to_dict(RetryEvent(run_id=self.run_id, content=notice))
                continue

            final = str(payload).strip()
            self.history.append({"role": "assistant", "content": final, "created_at": now()})
            yield event_to_dict(FinalEvent(run_id=self.run_id, content=final))
            return

        content = self._step_limit_summary(user_message, tool_steps)
        self.history.append({"role": "assistant", "content": content, "created_at": now()})
        yield event_to_dict(StepLimitEvent(run_id=self.run_id, content=content))

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

    def _cancelled_event(self) -> dict[str, Any]:
        content = "已停止当前运行。"
        self._append_cancelled_history(content)
        return event_to_dict(CancelledEvent(run_id=self.run_id, content=content))

    def _append_tool_history(self, event: ToolResultEvent) -> None:
        self.history.append(
            {
                "role": "tool",
                "name": event.tool,
                "args": event.args,
                "content": event.content,
                "is_error": event.is_error,
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
        self.history.append({"role": "assistant", "content": content, "created_at": now()})

    async def _call_model(self, prompt: str) -> str:
        complete = getattr(self.model, "complete", None)
        if callable(complete):
            result = complete(prompt)
            if inspect.isawaitable(result):
                value = await result
            else:
                value = result
            return str(value)

        ainvoke = getattr(self.model, "ainvoke", None)
        if callable(ainvoke):
            messages = self._build_ainvoke_messages(prompt)
            response = await ainvoke(messages)
            content = getattr(response, "content", response)
            return content if isinstance(content, str) else str(content)
        raise TypeError("model must provide complete(prompt) or ainvoke(messages)")

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
