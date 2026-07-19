"""Single-tool execution pipeline for the coding agent."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, cast

from sage_harness import SandboxOperation, SandboxPolicyError, SandboxPort

from core.coding.context import WorkspaceContext
from core.coding.engine.events import (
    ApprovalGrantedEvent,
    ApprovalRequiredEvent,
    CancelledEvent,
    RunEventBase,
    ToolCallEvent,
    ToolResultEvent,
)
from core.coding.engine.helpers import normalize_tool_payload
from core.coding.tool_executor.approval import (
    ApprovalChoice,
    ApprovalManager,
    check_dangerous_command,
)
from core.coding.tool_executor.permissions import PermissionChecker
from core.coding.tool_executor.policy import ToolPolicyChecker
from core.coding.tools.base import RegisteredTool, ToolResult
from core.coding.tools.registry import validate_tool

_SANDBOX_OPERATIONS = frozenset(
    {"list_files", "read_file", "search", "write_file", "patch_file", "run_shell"}
)
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApprovalRequirement:
    """Approval metadata for one validated, policy-allowed tool call."""

    tool: str
    args: dict[str, Any]
    description: str
    pattern_key: str


class ToolExecutor:
    """Execute one normalized tool payload and yield typed runtime events."""

    def __init__(
        self,
        tools: dict[str, RegisteredTool],
        workspace: WorkspaceContext,
        permission_checker: PermissionChecker,
        policy_checker: ToolPolicyChecker,
        approval_manager: ApprovalManager | None = None,
        session_id: str = "",
        should_stop: Callable[[], bool] | None = None,
        run_id: str = "",
        sandbox: SandboxPort | None = None,
    ) -> None:
        self.tools = tools
        self.workspace = workspace
        self.permission_checker = permission_checker
        self.policy_checker = policy_checker
        self.approval_manager = approval_manager
        self.session_id = session_id
        self.should_stop = should_stop or (lambda: False)
        self.run_id = run_id
        self.sandbox = sandbox

    def approval_requirement(self, payload: Any) -> ApprovalRequirement | None:
        """Return approval metadata without waiting on the legacy queue."""
        name, args = normalize_tool_payload(payload)
        tool = self.tools.get(name)
        if tool is None:
            return None
        try:
            args = (
                tool.validate_arguments(args)
                if tool.argument_validator is not None
                else validate_tool(self.workspace, name, args)
            )
        except ValueError:
            return None
        permission = self.permission_checker.check(tool, args, self.workspace)
        if not permission.allowed or permission.reason != "approval_required":
            return None
        policy = self.policy_checker.check(tool, args)
        if not policy.allowed:
            return None
        description, pattern_key = self._approval_metadata(tool.name, args)
        if (
            self.approval_manager is not None
            and self.session_id
            and self.approval_manager.is_session_approved(self.session_id, pattern_key)
        ):
            return None
        return ApprovalRequirement(
            tool=tool.name,
            args=args,
            description=description,
            pattern_key=pattern_key,
        )

    async def execute(
        self,
        payload: Any,
        *,
        approval_choice: ApprovalChoice | None = None,
    ) -> AsyncIterator[RunEventBase]:
        """Yield typed events for a single tool execution request."""
        if self.should_stop():
            yield self._cancelled_event()
            return

        name, args = normalize_tool_payload(payload)
        tool = self.tools.get(name)
        if tool is None:
            yield ToolResultEvent(
                run_id=self.run_id,
                tool=name,
                args=args,
                content=f"unknown tool: {name}",
                is_error=True,
            )
            return

        try:
            args = (
                tool.validate_arguments(args)
                if tool.argument_validator is not None
                else validate_tool(self.workspace, name, args)
            )
        except ValueError as exc:
            yield self._tool_result_event(
                name,
                args,
                ToolResult(content=str(exc), is_error=True),
            )
            return

        permission = self.permission_checker.check(tool, args, self.workspace)
        if not permission.allowed:
            yield ToolResultEvent(
                run_id=self.run_id,
                tool=name,
                args=args,
                content=permission.reason,
                is_error=True,
                security_event_type=permission.security_event_type,
            )
            return

        policy = self.policy_checker.check(tool, args)
        if not policy.allowed:
            yield ToolResultEvent(
                run_id=self.run_id,
                tool=name,
                args=args,
                content=policy.message,
                is_error=True,
                policy_reason=policy.reason,
            )
            return

        if permission.reason == "approval_required" and approval_choice is not None:
            if approval_choice == "deny":
                yield self._tool_result_event(
                    name,
                    args,
                    ToolResult(content="approval denied", is_error=True),
                )
                return
            yield ApprovalGrantedEvent(run_id=self.run_id, tool=name)
        elif permission.reason == "approval_required":
            approved = False
            should_return = False
            approval_stream = self._approval_events(tool, args)
            try:
                async for event in approval_stream:
                    if event.type == "approval_granted":
                        approved = True
                        yield event
                        continue
                    yield event
                    if event.type in {"tool_result", "cancelled"}:
                        should_return = True
                        break
            finally:
                await approval_stream.aclose()
            if should_return:
                return
            if not approved:
                return

        yield ToolCallEvent(run_id=self.run_id, tool=name, args=args)
        if self.sandbox is not None and name in _SANDBOX_OPERATIONS:
            try:
                sandbox_result = await self.sandbox.invoke(
                    cast(SandboxOperation, name),
                    args,
                )
                result = ToolResult(
                    content=sandbox_result.content,
                    is_error=sandbox_result.is_error,
                    error_code=sandbox_result.error_code,
                    retryable=sandbox_result.retryable,
                )
            except SandboxPolicyError as exc:
                result = ToolResult(content=str(exc), is_error=True)
            except Exception as exc:
                logger.warning("Sandbox operation failed (%s)", type(exc).__name__)
                result = ToolResult(content="sandbox operation failed", is_error=True)
        else:
            result = tool.execute(args)
        if self.should_stop():
            yield self._cancelled_event()
            return
        yield self._tool_result_event(name, args, result)

    async def _approval_events(
        self,
        tool: RegisteredTool,
        args: dict[str, Any],
    ) -> AsyncGenerator[RunEventBase]:
        if self.approval_manager is None or not self.session_id:
            yield self._tool_result_event(
                tool.name,
                args,
                ToolResult(content="approval manager is not configured", is_error=True),
            )
            return

        description, pattern_key = self._approval_metadata(tool.name, args)

        if self.approval_manager.is_session_approved(self.session_id, pattern_key):
            yield ApprovalGrantedEvent(run_id=self.run_id, tool=tool.name)
            return

        entry = self.approval_manager.submit(
            self.session_id,
            tool.name,
            args,
            description,
            pattern_key,
        )
        yield ApprovalRequiredEvent(
            run_id=self.run_id,
            approval_id=entry.approval_id,
            tool=tool.name,
            args=args,
            description=description,
            pattern_key=pattern_key,
        )

        waited_seconds = 0
        while waited_seconds < 300:
            if self.should_stop():
                yield self._tool_result_event(
                    tool.name,
                    args,
                    ToolResult(content="approval cancelled", is_error=True),
                )
                yield self._cancelled_event()
                return
            if await asyncio.to_thread(entry.event.wait, 1.0):
                break
            waited_seconds += 1
        if not entry.event.is_set():
            yield self._tool_result_event(
                tool.name,
                args,
                ToolResult(content="approval timed out", is_error=True),
            )
            return
        if entry.result == "deny":
            yield self._tool_result_event(
                tool.name,
                args,
                ToolResult(content="approval denied", is_error=True),
            )
            return
        yield ApprovalGrantedEvent(run_id=self.run_id, tool=tool.name)

    @staticmethod
    def _approval_metadata(tool_name: str, args: dict[str, Any]) -> tuple[str, str]:
        description = _approval_description(tool_name)
        pattern_key = f"tool:{tool_name}"
        if tool_name == "knowledge_learn":
            description = "保存本轮引用证据到知识库前需要确认。"
        elif tool_name == "remember":
            description = "保存事实到长期工作区记忆前需要确认。"
        elif tool_name == "run_shell":
            dangerous, command_description, command_pattern = check_dangerous_command(
                str(args.get("command", ""))
            )
            if dangerous:
                description = command_description
                pattern_key = f"shell:{command_pattern}"
        return description, pattern_key

    def _tool_result_event(
        self,
        name: str,
        args: dict[str, Any],
        result: ToolResult,
    ) -> ToolResultEvent:
        return ToolResultEvent(
            run_id=self.run_id,
            tool=name,
            args=args,
            content=result.content,
            is_error=result.is_error,
            error_code=result.error_code,
            retryable=result.retryable,
        )

    def _cancelled_event(self) -> CancelledEvent:
        return CancelledEvent(run_id=self.run_id, content="已停止当前运行。")


def _approval_description(tool_name: str) -> str:
    descriptions = {
        "write_file": "写入文件前需要确认。",
        "patch_file": "修改文件前需要确认。",
        "run_shell": "执行 Shell 命令前需要确认。",
        "knowledge_learn": "保存本轮引用证据到知识库前需要确认。",
        "remember": "保存事实到长期工作区记忆前需要确认。",
    }
    return descriptions.get(tool_name, f"调用工具 {tool_name} 前需要确认。")
