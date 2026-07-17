"""Application-neutral contracts for awaited child-agent execution."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

SubagentTerminalStatus = Literal["succeeded", "failed", "cancelled", "timed_out"]
SubagentCancelReason = Literal["parent_cancelled", "timeout"]


@dataclass(frozen=True, slots=True)
class SubagentLimits:
    """Server-owned limits for one parent graph."""

    max_concurrent: int = 3
    max_total_per_run: int = 6
    max_depth: int = 1

    def __post_init__(self) -> None:
        if not 1 <= self.max_concurrent <= 4:
            raise ValueError("max_concurrent must be between 1 and 4")
        if not 1 <= self.max_total_per_run <= 50:
            raise ValueError("max_total_per_run must be between 1 and 50")
        if not 0 <= self.max_depth <= 4:
            raise ValueError("max_depth must be between 0 and 4")


@dataclass(frozen=True, slots=True)
class SubagentToolConfig:
    """Capabilities inherited by children launched through the task tool."""

    allowed_types: frozenset[str] = frozenset({"Explore"})
    tool_scope: tuple[str, ...] = ("list_files", "read_file", "search")
    token_budget: int = 24_000
    timeout_seconds: float = 90.0
    max_steps: int = 12

    def __post_init__(self) -> None:
        if not self.allowed_types or any(not item.strip() for item in self.allowed_types):
            raise ValueError("allowed_types must contain non-empty names")
        if not self.tool_scope or any(not item.strip() for item in self.tool_scope):
            raise ValueError("tool_scope must contain non-empty tool names")
        if self.token_budget < 1:
            raise ValueError("token_budget must be positive")
        if not 0.1 <= self.timeout_seconds <= 1800:
            raise ValueError("timeout_seconds must be between 0.1 and 1800")
        if not 1 <= self.max_steps <= 50:
            raise ValueError("max_steps must be between 1 and 50")


@dataclass(frozen=True, slots=True)
class SubagentRequest:
    """One server-bound child execution request."""

    parent_thread_id: str
    parent_run_id: str
    child_run_id: str
    description: str
    prompt: str
    subagent_type: str
    workspace_id: str
    workspace_path: str
    tool_scope: tuple[str, ...]
    token_budget: int
    timeout_seconds: float
    max_steps: int
    depth: int = 1

    def __post_init__(self) -> None:
        for field_name in (
            "parent_thread_id",
            "parent_run_id",
            "child_run_id",
            "description",
            "prompt",
            "subagent_type",
            "workspace_id",
            "workspace_path",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be empty")
        if not self.tool_scope:
            raise ValueError("tool_scope must not be empty")
        if self.token_budget < 1 or self.timeout_seconds <= 0 or self.max_steps < 1:
            raise ValueError("subagent budgets must be positive")
        if self.depth < 1:
            raise ValueError("depth must be positive")


@dataclass(frozen=True, slots=True)
class SubagentResult:
    """Terminal child result returned to the parent task tool."""

    child_run_id: str
    status: SubagentTerminalStatus
    result: str = ""
    result_ref: str = ""
    error_code: str = ""

    def __post_init__(self) -> None:
        if not self.child_run_id.strip():
            raise ValueError("child_run_id must not be empty")
        if self.result_ref and not self.result_ref.startswith("subagent://"):
            raise ValueError("result_ref must use the subagent scheme")


class SubagentExecutorPort(Protocol):
    """Application-owned child runtime used by the neutral task tool."""

    def execute(self, request: SubagentRequest) -> Awaitable[SubagentResult]: ...

    def cancel(
        self,
        child_run_id: str,
        reason: SubagentCancelReason = "parent_cancelled",
    ) -> Awaitable[None]: ...


CancelCheck = Callable[[], bool]


def derive_child_run_id(thread_id: str, run_id: str, tool_call_id: str) -> str:
    """Derive a stable non-host child identity from server and graph identities."""
    digest = hashlib.sha256(f"{thread_id}\0{run_id}\0{tool_call_id}".encode()).hexdigest()[:20]
    return f"child_{digest}"


__all__ = [
    "CancelCheck",
    "SubagentCancelReason",
    "SubagentExecutorPort",
    "SubagentLimits",
    "SubagentRequest",
    "SubagentResult",
    "SubagentTerminalStatus",
    "SubagentToolConfig",
    "derive_child_run_id",
]
