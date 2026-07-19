"""Worker runtime assembly for coding subagents."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from core.coding.context import ContextManager, WorkspaceContext
from core.coding.engine.engine import Engine
from core.coding.multiagent.execution import WorkerTask
from core.coding.tool_executor import PermissionChecker, ToolPolicyChecker
from core.coding.tools.registry import build_tool_registry
from core.coding.usage_store import UsageSample


class WorkerTaskCancelled(RuntimeError):
    """The parent cancelled a child before it produced a final result."""


class WorkerTaskBudgetExceeded(RuntimeError):
    """The child reached its cumulative model token budget."""


async def run_worker_task(
    task: WorkerTask,
    workspace: WorkspaceContext,
    model_factory: Callable[[], Any],
    *,
    tool_scope: tuple[str, ...] | None = None,
    should_stop: Callable[[], bool] | None = None,
    token_budget: int | None = None,
    max_steps: int = 20,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    tools: dict[str, Any] | None = None,
    usage_sink: Callable[[UsageSample], None] | None = None,
) -> str:
    """Run one worker task and return its final response."""
    if tools is None:
        tools = build_tool_registry(workspace)
    if tool_scope is not None:
        tools = {name: tool for name, tool in tools.items() if name in set(tool_scope)}
    used_tokens = 0

    def record_usage(attempt: int, usage: UsageSample) -> None:
        _ = attempt
        nonlocal used_tokens
        used_tokens += usage.total_tokens or (
            (usage.input_tokens or 0) + (usage.output_tokens or 0)
        )
        if usage_sink is not None:
            usage_sink(usage)

    def stopped() -> bool:
        return bool(should_stop and should_stop()) or bool(
            token_budget is not None and used_tokens >= token_budget
        )

    permission = PermissionChecker(
        approval_policy="auto",
        write_scope=task.write_scope,
        plan_mode=task.subagent_type == "Explore",
    )
    engine = Engine(
        model=model_factory(),
        workspace=workspace,
        tools=tools,
        context_manager=ContextManager(),
        permission_checker=permission,
        policy_checker=ToolPolicyChecker(workspace),
        should_stop=stopped,
        run_id=task.id,
        max_steps=max_steps,
        model_usage_sink=record_usage,
    )
    final = ""
    async for event in engine.run_turn(task.prompt):
        if event_sink is not None:
            event_sink(event)
        if event["type"] == "final":
            final = str(event["content"])
        elif event["type"] == "cancelled":
            if token_budget is not None and used_tokens >= token_budget:
                raise WorkerTaskBudgetExceeded("worker token budget exceeded")
            raise WorkerTaskCancelled("worker cancelled")
    return final


__all__ = ["WorkerTaskBudgetExceeded", "WorkerTaskCancelled", "run_worker_task"]
