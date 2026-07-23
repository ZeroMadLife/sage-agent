"""Decorator-backed coding tool registry."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from core.coding.context import WorkspaceContext
from core.coding.tools.base import RegisteredTool, ToolContext, ToolResult
from core.coding.tools.schemas import ToolSearchArgs, first_error_message

ToolHandler = Callable[[WorkspaceContext, dict[str, Any], ToolContext | None], ToolResult | str]
_WORKSPACE_PATH_TOOLS = {"list_files", "read_file", "search", "write_file", "patch_file"}


class ToolArgumentValidationError(ValueError):
    """A recoverable schema error in model-provided tool arguments."""

    def __init__(self, tool_name: str, message: str, schema: dict[str, Any]) -> None:
        super().__init__(message)
        self.tool_name = tool_name
        self.schema = schema


TOOL_MODULES = (
    "core.coding.tools.file_tools",
    "core.coding.tools.shell_tool",
    "core.coding.tools.todo_tools",
    "core.coding.tools.plan_tools",
    "core.coding.tools.agent_tools",
    "core.coding.tools.travel_tools",
    "core.coding.tools.memory_tools",
    "core.coding.tools.knowledge_tools",
)


@dataclass(frozen=True)
class ToolDefinition:
    """Metadata and handler registered by one tool module."""

    name: str
    schema: dict[str, Any]
    description: str
    risky: bool
    schema_model: type[BaseModel]
    handler: ToolHandler
    category: str = "general"
    requires_approval: bool | None = None
    timeout: float = 30.0
    deferred: bool = False


_TOOL_DEFINITIONS: dict[str, ToolDefinition] = {}
_MODULES_LOADED = False


def register_tool(
    *,
    name: str,
    description: str,
    schema: dict[str, Any],
    schema_model: type[BaseModel],
    risky: bool,
    category: str = "general",
    requires_approval: bool | None = None,
    timeout: float = 30.0,
    deferred: bool = False,
) -> Callable[[ToolHandler], ToolHandler]:
    """Register a tool handler from its owning module."""

    def decorator(handler: ToolHandler) -> ToolHandler:
        _TOOL_DEFINITIONS[name] = ToolDefinition(
            name=name,
            schema=dict(schema),
            description=description,
            risky=risky,
            schema_model=schema_model,
            handler=handler,
            category=category,
            requires_approval=requires_approval,
            timeout=timeout,
            deferred=deferred,
        )
        return handler

    return decorator


def registered_tool_definitions() -> dict[str, ToolDefinition]:
    """Return decorator-registered tool definitions."""
    _ensure_default_modules_loaded()
    return dict(_TOOL_DEFINITIONS)


def build_tool_registry(
    workspace: WorkspaceContext,
    tool_context: ToolContext | None = None,
    activated_tools: set[str] | None = None,
) -> dict[str, RegisteredTool]:
    """Build coding tools for one workspace."""
    _ensure_default_modules_loaded()
    activated = activated_tools if activated_tools is not None else set()
    definitions = {
        **_TOOL_DEFINITIONS,
        "tool_search": _tool_search_definition(activated),
    }

    return {
        name: RegisteredTool(
            name=name,
            schema=dict(definition.schema),
            description=definition.description,
            risky=definition.risky,
            runner=_make_runner(workspace, definition, tool_context),
            category=definition.category,
            requires_approval=definition.requires_approval,
            timeout=definition.timeout,
            deferred=definition.deferred,
        )
        for name, definition in definitions.items()
    }


def get_active_tools(
    tools: dict[str, RegisteredTool],
    activated: set[str],
) -> dict[str, RegisteredTool]:
    """Return resident tools plus session-activated deferred tools."""
    return {name: tool for name, tool in tools.items() if not tool.deferred or name in activated}


def execute_tool(
    workspace: WorkspaceContext,
    name: str,
    args: dict[str, Any] | None,
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Validate and execute one registered tool."""
    _ensure_default_modules_loaded()
    definition = _TOOL_DEFINITIONS.get(name)
    if definition is None:
        return ToolResult(content=f"unknown tool: {name}", is_error=True)
    try:
        validated = validate_tool(workspace, name, args or {})
        result = definition.handler(workspace, validated, tool_context)
    except Exception as exc:
        return ToolResult(content=str(exc), is_error=True)
    if isinstance(result, ToolResult):
        return result
    return ToolResult(content=str(result))


def validate_tool(
    workspace: WorkspaceContext,
    name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Validate pydantic schema and workspace-aware constraints."""
    validated = validate_tool_preflight(workspace, name, args)

    if name == "list_files":
        path = workspace.path(str(validated["path"]))
        if not path.is_dir():
            raise ValueError("path is not a directory")
    elif name == "read_file":
        path = workspace.path(str(validated["path"]))
        if not path.is_file():
            raise ValueError("path is not a file")
    elif name == "search":
        workspace.path(str(validated["path"]))
    elif name == "write_file":
        path = workspace.path(str(validated["path"]))
        if path.exists() and path.is_dir():
            raise ValueError("path is a directory")
    elif name == "patch_file":
        path = workspace.path(str(validated["path"]))
        if not path.is_file():
            raise ValueError("path is not a file")
        old_text = str(validated["old_text"])
        text = path.read_text(encoding="utf-8", errors="replace")
        count = text.count(old_text)
        if count != 1:
            raise ValueError(f"old_text must occur exactly once, found {count}")
    return validated


def validate_tool_preflight(
    workspace: WorkspaceContext,
    name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Validate schema and path boundaries without depending on workspace state."""
    validated = validate_tool_arguments(name, args)
    if name not in _WORKSPACE_PATH_TOOLS:
        return validated
    try:
        workspace.path(str(validated["path"]))
    except ValueError as exc:
        definition = _TOOL_DEFINITIONS[name]
        raise ToolArgumentValidationError(name, str(exc), dict(definition.schema)) from exc
    return validated


def validate_tool_arguments(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Validate only declared arguments without reading or resolving workspace paths."""
    _ensure_default_modules_loaded()
    definition = _TOOL_DEFINITIONS.get(name)
    if definition is None and name == "tool_search":
        definition = _tool_search_definition(set())
    if definition is None:
        raise ValueError(f"unknown tool: {name}")
    try:
        validated = definition.schema_model.model_validate(args).model_dump()
    except ValidationError as exc:
        raise ToolArgumentValidationError(
            name,
            first_error_message(exc),
            dict(definition.schema),
        ) from exc

    return validated


def _make_runner(
    workspace: WorkspaceContext,
    definition: ToolDefinition,
    tool_context: ToolContext | None,
) -> Callable[[dict[str, Any]], ToolResult | str]:
    def runner(args: dict[str, Any]) -> ToolResult | str:
        validated = validate_tool(workspace, definition.name, args or {})
        return definition.handler(workspace, validated, tool_context)

    return runner


def _tool_search_definition(activated_tools: set[str]) -> ToolDefinition:
    return ToolDefinition(
        name="tool_search",
        schema={"query": "str"},
        description=(
            "Search for available deferred tools by name or keyword and activate matching tools."
        ),
        risky=False,
        schema_model=ToolSearchArgs,
        handler=_make_tool_search_handler(activated_tools),
        category="meta",
        deferred=False,
    )


def _make_tool_search_handler(activated_tools: set[str]) -> ToolHandler:
    def handler(
        workspace: WorkspaceContext,
        args: dict[str, Any],
        tool_context: ToolContext | None = None,
    ) -> ToolResult:
        _ = workspace, tool_context
        query = str(args.get("query", "")).strip().lower()
        definitions = registered_tool_definitions()
        matches = [
            definition
            for definition in definitions.values()
            if definition.deferred
            and definition.name not in activated_tools
            and (
                query in definition.name.lower()
                or query in definition.description.lower()
                or query in definition.category.lower()
            )
        ]
        if not matches:
            return ToolResult(content="No matching deferred tools found.")
        for match in matches:
            activated_tools.add(match.name)
        payload = [
            {
                "name": match.name,
                "description": match.description,
                "schema": match.schema,
            }
            for match in matches
        ]
        return ToolResult(content=json.dumps(payload, ensure_ascii=False, indent=2))

    return handler


def _ensure_default_modules_loaded() -> None:
    global _MODULES_LOADED
    if _MODULES_LOADED:
        return
    _MODULES_LOADED = True
    for module_name in TOOL_MODULES:
        importlib.import_module(module_name)
