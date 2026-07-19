"""Tool abstraction for the coding agent."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from typing import Any

_TOOL_EXECUTOR = ThreadPoolExecutor(max_workers=16, thread_name_prefix="sage-tool")


@dataclass(frozen=True)
class ToolResult:
    """Result returned by a coding tool."""

    content: str
    is_error: bool = False
    error_code: str | None = None
    retryable: bool | None = None


ToolRunner = Callable[[dict[str, Any]], ToolResult | str]
ToolArgumentValidator = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class RegisteredTool:
    """A registered coding tool with schema, risk, and executable runner."""

    name: str
    schema: dict[str, Any]
    description: str
    risky: bool
    runner: ToolRunner
    category: str = "general"
    requires_approval: bool | None = None
    timeout: float = 30.0
    deferred: bool = False
    argument_validator: ToolArgumentValidator | None = None

    def __post_init__(self) -> None:
        """Default approval metadata follows risk metadata."""
        if self.requires_approval is None:
            object.__setattr__(self, "requires_approval", self.risky)

    @property
    def read_only(self) -> bool:
        """Return whether this tool is safe to run without write approval."""
        return not self.risky

    def execute(self, args: dict[str, Any] | None = None) -> ToolResult:
        """Execute the tool and convert validation/runtime failures to ToolResult."""
        try:
            validated = self.validate_arguments(args or {})
        except Exception as exc:
            return ToolResult(
                content=str(exc),
                is_error=True,
                error_code="invalid_arguments",
                retryable=False,
            )
        future = _TOOL_EXECUTOR.submit(self.runner, validated)
        try:
            result = future.result(timeout=self.timeout)
        except TimeoutError:
            future.cancel()
            return ToolResult(
                content=f"tool timed out after {self.timeout:g}s",
                is_error=True,
                error_code="tool_timeout",
                retryable=True,
            )
        except Exception as exc:
            return ToolResult(
                content=str(exc),
                is_error=True,
                error_code="tool_runtime_error",
                retryable=False,
            )
        if isinstance(result, ToolResult):
            return result
        return ToolResult(content=str(result))

    def validate_arguments(self, args: dict[str, Any]) -> dict[str, Any]:
        """Validate server-bound arguments for static or dynamically adapted tools."""
        return self.argument_validator(args) if self.argument_validator is not None else args


@dataclass
class ToolContext:
    """Optional runtime components used by extended tools."""

    runtime: Any | None = None
    todo_ledger: Any | None = None
    worker_manager: Any | None = None
    knowledge_store: Any | None = None
