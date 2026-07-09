"""Worker-agent tools for the coding agent."""

from __future__ import annotations

import json
from typing import Any

from core.coding.context import WorkspaceContext
from core.coding.tools.base import ToolContext, ToolResult
from core.coding.tools.registry import register_tool
from core.coding.tools.schemas import AgentArgs, SendMessageArgs, TaskStopArgs


@register_tool(
    name="agent",
    description="Launch a bounded worker or read-only Explore subagent.",
    schema={
        "description": "str",
        "prompt": "str",
        "subagent_type": "str='worker'",
        "write_scope": "list[str]=[]",
    },
    schema_model=AgentArgs,
    risky=False,
    category="agent",
    deferred=True,
)
def agent(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    _ = workspace
    manager = _require_context_attr(tool_context, "worker_manager")
    payload = manager.spawn(
        description=str(args["description"]),
        prompt=str(args["prompt"]),
        subagent_type=str(args.get("subagent_type", "worker")),
        write_scope=args.get("write_scope", []),
    )
    return ToolResult(content=json.dumps(payload, ensure_ascii=False, sort_keys=True))


@register_tool(
    name="send_message",
    description="Continue an existing worker by id.",
    schema={"to": "str", "message": "str"},
    schema_model=SendMessageArgs,
    risky=False,
    category="agent",
    deferred=True,
)
def send_message(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    _ = workspace
    manager = _require_context_attr(tool_context, "worker_manager")
    payload = manager.send_message(str(args["to"]), str(args["message"]))
    return ToolResult(content=json.dumps(payload, ensure_ascii=False, sort_keys=True))


@register_tool(
    name="task_stop",
    description="Stop a worker by id.",
    schema={"task_id": "str"},
    schema_model=TaskStopArgs,
    risky=False,
    category="agent",
    deferred=True,
)
def task_stop(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    _ = workspace
    manager = _require_context_attr(tool_context, "worker_manager")
    payload = manager.stop(str(args["task_id"]))
    return ToolResult(content=json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _require_context_attr(context: ToolContext | None, attr: str) -> Any:
    value = getattr(context, attr, None) if context is not None else None
    if value is None:
        raise ValueError(f"{attr} is not configured")
    return value
