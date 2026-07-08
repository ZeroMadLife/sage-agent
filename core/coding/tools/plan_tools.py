"""Plan mode tools for the coding agent."""

from __future__ import annotations

from typing import Any

from core.coding.tools.base import ToolContext, ToolResult
from core.coding.tools.registry import register_tool
from core.coding.tools.schemas import EnterPlanModeArgs, ExitPlanModeArgs
from core.coding.workspace import WorkspaceContext


@register_tool(
    name="enter_plan_mode",
    description="Enter plan mode for a named planning topic.",
    schema={"topic": "str", "path": "str?"},
    schema_model=EnterPlanModeArgs,
    risky=False,
    category="plan",
    deferred=True,
)
def enter_plan_mode(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    _ = workspace
    runtime = _require_context_attr(tool_context, "runtime")
    path = runtime.enter_plan_mode(str(args["topic"]), path=args.get("path"))
    return ToolResult(content=f"mode: plan\nplan path: {path}")


@register_tool(
    name="exit_plan_mode",
    description="Exit plan mode and return to default mode.",
    schema={},
    schema_model=ExitPlanModeArgs,
    risky=False,
    category="plan",
    deferred=True,
)
def exit_plan_mode(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    _ = workspace, args
    runtime = _require_context_attr(tool_context, "runtime")
    runtime.exit_plan_mode()
    return ToolResult(content="mode: default")


def _require_context_attr(context: ToolContext | None, attr: str) -> Any:
    value = getattr(context, attr, None) if context is not None else None
    if value is None:
        raise ValueError(f"{attr} is not configured")
    return value
