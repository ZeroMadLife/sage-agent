"""Todo ledger tools for the coding agent."""

from __future__ import annotations

from typing import Any

from core.coding.context import WorkspaceContext
from core.coding.tools.base import ToolContext, ToolResult
from core.coding.tools.registry import register_tool
from core.coding.tools.schemas import TodoAddArgs, TodoListArgs, TodoUpdateArgs


@register_tool(
    name="todo_add",
    description="Add an item to the session task ledger.",
    schema={
        "content": "str",
        "status": "str='pending'",
        "priority": "str='normal'",
        "note": "str=''",
    },
    schema_model=TodoAddArgs,
    risky=False,
    category="todo",
    deferred=True,
)
def todo_add(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    _ = workspace
    ledger = _require_context_attr(tool_context, "todo_ledger")
    item = ledger.add(
        str(args["content"]),
        status=str(args.get("status", "pending")),
        priority=str(args.get("priority", "normal")),
        note=str(args.get("note", "")),
    )
    return ToolResult(
        content=f"added {item['id']} [{item['status']}] {item['priority']} - {item['content']}"
    )


@register_tool(
    name="todo_update",
    description="Update an item in the session task ledger.",
    schema={
        "todo_id": "str",
        "status": "str?",
        "content": "str?",
        "priority": "str?",
        "note": "str?",
    },
    schema_model=TodoUpdateArgs,
    risky=False,
    category="todo",
    deferred=True,
)
def todo_update(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    _ = workspace
    ledger = _require_context_attr(tool_context, "todo_ledger")
    item = ledger.update(
        str(args["todo_id"]),
        status=args.get("status"),
        content=args.get("content"),
        priority=args.get("priority"),
        note=args.get("note"),
    )
    return ToolResult(
        content=f"updated {item['id']} [{item['status']}] {item['priority']} - {item['content']}"
    )


@register_tool(
    name="todo_list",
    description="List the session task ledger.",
    schema={},
    schema_model=TodoListArgs,
    risky=False,
    category="todo",
    deferred=True,
)
def todo_list(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    _ = workspace, args
    ledger = _require_context_attr(tool_context, "todo_ledger")
    return ToolResult(content=str(ledger.render_list()))


def _require_context_attr(context: ToolContext | None, attr: str) -> Any:
    value = getattr(context, attr, None) if context is not None else None
    if value is None:
        raise ValueError(f"{attr} is not configured")
    return value
