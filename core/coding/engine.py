"""Async generator engine for one coding-agent turn."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable
from typing import Any, Protocol

from core.coding.approval import ApprovalManager, check_dangerous_command
from core.coding.context_manager import ContextManager
from core.coding.engine_helpers import (
    build_tool_descriptions,
    normalize_tool_payload,
    step_limit_summary,
)
from core.coding.model_output import parse
from core.coding.permissions import PermissionChecker
from core.coding.tool_policy import ToolPolicyChecker
from core.coding.tools.base import RegisteredTool, ToolResult
from core.coding.tools.registry import get_active_tools
from core.coding.workspace import WorkspaceContext, now


class ModelClient(Protocol):
    """Minimal model contract used by the coding engine."""

    async def complete(self, prompt: str) -> str:
        """Return raw model text."""
        ...


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
        self.max_steps = max_steps

    async def run_turn(self, user_message: str) -> AsyncIterator[dict[str, Any]]:
        """Run one coding turn and yield streamable events."""
        self.history.append({"role": "user", "content": user_message, "created_at": now()})
        tool_steps = 0
        attempts = 0

        while tool_steps < self.max_steps and attempts < self.max_steps + 2:
            if self.should_stop():
                yield self._cancelled_event()
                return
            attempts += 1
            prompt, metadata = self.context_manager.build(
                user_message=user_message,
                history=self.history,
                tools=self._tool_descriptions(),
            )
            yield {
                "type": "model_requested",
                "attempts": attempts,
                "tool_steps": tool_steps,
                "prompt_chars": metadata["prompt_chars"],
            }

            raw = await self._call_model(prompt)
            if self.should_stop():
                yield self._cancelled_event()
                return
            kind, payload = parse(raw)
            yield {"type": "model_parsed", "kind": kind}

            if kind in {"tool", "tools"}:
                tool_payloads = [payload] if kind == "tool" else list(payload)
                for tool_payload in tool_payloads:
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
                yield {"type": "retry", "content": notice}
                continue

            final = str(payload).strip()
            self.history.append({"role": "assistant", "content": final, "created_at": now()})
            yield {"type": "final", "content": final}
            return

        content = self._step_limit_summary(user_message, tool_steps)
        self.history.append({"role": "assistant", "content": content, "created_at": now()})
        yield {"type": "step_limit", "content": content}

    async def _execute_tool_payload(self, payload: Any) -> AsyncIterator[dict[str, Any]]:
        if self.should_stop():
            yield self._cancelled_event()
            return
        name, args = normalize_tool_payload(payload)
        tool = self.tools.get(name)
        if tool is None:
            result = ToolResult(content=f"unknown tool: {name}", is_error=True)
            yield self._tool_result_event(name, args, result)
            return

        permission = self.permission_checker.check(tool, args, self.workspace)
        if not permission.allowed:
            result = ToolResult(content=permission.reason, is_error=True)
            event = self._tool_result_event(name, args, result)
            event["security_event_type"] = permission.security_event_type
            yield event
            return

        policy = self.policy_checker.check(tool, args)
        if not policy.allowed:
            result = ToolResult(content=policy.message, is_error=True)
            event = self._tool_result_event(name, args, result)
            event["policy_reason"] = policy.reason
            yield event
            return

        if permission.reason == "approval_required":
            approved = False
            async for event in self._approval_events(tool, args):
                if event["type"] == "approval_granted":
                    approved = True
                    continue
                yield event
                if event["type"] == "tool_result":
                    return
            if not approved:
                return

        yield {"type": "tool_call", "tool": name, "args": args}
        result = tool.execute(args)
        if self.should_stop():
            yield self._cancelled_event()
            return
        yield self._tool_result_event(name, args, result)

    async def _approval_events(
        self,
        tool: RegisteredTool,
        args: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        if self.approval_manager is None or not self.session_id:
            result = ToolResult(content="approval manager is not configured", is_error=True)
            yield self._tool_result_event(tool.name, args, result)
            return

        description = f"{tool.name} requires approval."
        pattern_key = f"tool:{tool.name}"
        if tool.name == "run_shell":
            dangerous, command_description, command_pattern = check_dangerous_command(
                str(args.get("command", ""))
            )
            if dangerous:
                description = command_description
                pattern_key = f"shell:{command_pattern}"

        if self.approval_manager.is_session_approved(self.session_id, pattern_key):
            yield {"type": "approval_granted", "tool": tool.name}
            return

        entry = self.approval_manager.submit(
            self.session_id,
            tool.name,
            args,
            description,
            pattern_key,
        )
        yield {
            "type": "approval_required",
            "approval_id": entry.approval_id,
            "tool": tool.name,
            "args": args,
            "description": description,
            "pattern_key": pattern_key,
        }

        waited_seconds = 0
        while waited_seconds < 300:
            if self.should_stop():
                result = ToolResult(content="approval cancelled", is_error=True)
                yield self._tool_result_event(tool.name, args, result)
                yield self._cancelled_event()
                return
            if await asyncio.to_thread(entry.event.wait, 1.0):
                break
            waited_seconds += 1
        if not entry.event.is_set():
            result = ToolResult(content="approval timed out", is_error=True)
            yield self._tool_result_event(tool.name, args, result)
            return
        if entry.result == "deny":
            result = ToolResult(content="approval denied", is_error=True)
            yield self._tool_result_event(tool.name, args, result)
            return
        yield {"type": "approval_granted", "tool": tool.name}

    def _cancelled_event(self) -> dict[str, Any]:
        content = "已停止当前运行。"
        self.history.append({"role": "assistant", "content": content, "created_at": now()})
        return {"type": "cancelled", "content": content}

    def _tool_result_event(
        self,
        name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> dict[str, Any]:
        self.history.append(
            {
                "role": "tool",
                "name": name,
                "args": args,
                "content": result.content,
                "is_error": result.is_error,
                "created_at": now(),
            }
        )
        return {
            "type": "tool_result",
            "tool": name,
            "args": args,
            "content": result.content,
            "is_error": result.is_error,
        }

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
            response = await ainvoke([{"role": "user", "content": prompt}])
            content = getattr(response, "content", response)
            return content if isinstance(content, str) else str(content)
        raise TypeError("model must provide complete(prompt) or ainvoke(messages)")

    def _tool_descriptions(self) -> list[str]:
        active_tools = get_active_tools(self.tools, self.activated_tools)
        descriptions = build_tool_descriptions(active_tools)
        deferred = sorted(
            name
            for name, tool in self.tools.items()
            if tool.deferred and name not in self.activated_tools
        )
        if deferred:
            descriptions.append(
                "Deferred tools (use tool_search to activate): " + ", ".join(deferred)
            )
        return descriptions

    @staticmethod
    def _step_limit_summary(user_message: str, tool_steps: int) -> str:
        return step_limit_summary(user_message, tool_steps)
