"""Application-neutral contracts for awaited child-agent execution."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

SubagentTerminalStatus = Literal["succeeded", "failed", "cancelled", "timed_out"]
SubagentCancelReason = Literal["parent_cancelled", "timeout"]
SubagentProgressSink = Callable[[Mapping[str, object]], None]


@dataclass(frozen=True, slots=True)
class SubagentProfile:
    """One server-owned child capability and budget envelope."""

    name: str
    tool_scope: tuple[str, ...]
    token_budget: int
    timeout_seconds: float
    max_steps: int

    def __post_init__(self) -> None:
        normalized = self.name.strip().casefold()
        if not normalized:
            raise ValueError("subagent profile name must not be empty")
        if not self.tool_scope or any(not item.strip() for item in self.tool_scope):
            raise ValueError("subagent profile tool_scope must contain non-empty names")
        if self.token_budget < 1:
            raise ValueError("subagent profile token_budget must be positive")
        if not 0.1 <= self.timeout_seconds <= 1800:
            raise ValueError("subagent profile timeout_seconds must be between 0.1 and 1800")
        if not 1 <= self.max_steps <= 50:
            raise ValueError("subagent profile max_steps must be between 1 and 50")
        object.__setattr__(self, "name", normalized)
        object.__setattr__(
            self,
            "tool_scope",
            tuple(dict.fromkeys(item.strip() for item in self.tool_scope)),
        )


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

    allowed_types: frozenset[str] = frozenset({"explore"})
    tool_scope: tuple[str, ...] = ("list_files", "read_file", "search")
    token_budget: int = 24_000
    timeout_seconds: float = 90.0
    max_steps: int = 12
    profiles: tuple[SubagentProfile, ...] = ()

    def __post_init__(self) -> None:
        if not self.allowed_types or any(not item.strip() for item in self.allowed_types):
            raise ValueError("allowed_types must contain non-empty names")
        object.__setattr__(
            self,
            "allowed_types",
            frozenset(item.strip().casefold() for item in self.allowed_types),
        )
        if not self.tool_scope or any(not item.strip() for item in self.tool_scope):
            raise ValueError("tool_scope must contain non-empty tool names")
        object.__setattr__(
            self,
            "tool_scope",
            tuple(dict.fromkeys(item.strip() for item in self.tool_scope)),
        )
        if self.token_budget < 1:
            raise ValueError("token_budget must be positive")
        if not 0.1 <= self.timeout_seconds <= 1800:
            raise ValueError("timeout_seconds must be between 0.1 and 1800")
        if not 1 <= self.max_steps <= 50:
            raise ValueError("max_steps must be between 1 and 50")
        names = tuple(profile.name for profile in self.profiles)
        if len(names) != len(set(names)):
            raise ValueError("subagent profiles must have unique names")
        if any(name not in self.allowed_types for name in names):
            raise ValueError("subagent profiles must be included in allowed_types")

    def resolve(self, name: str) -> SubagentProfile | None:
        """Resolve one profile without allowing model-authored capability changes."""
        normalized = name.strip().casefold()
        if normalized not in self.allowed_types:
            return None
        configured = next(
            (profile for profile in self.profiles if profile.name == normalized),
            None,
        )
        if configured is not None:
            return configured
        return SubagentProfile(
            name=normalized,
            tool_scope=self.tool_scope,
            token_budget=self.token_budget,
            timeout_seconds=self.timeout_seconds,
            max_steps=self.max_steps,
        )


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
        object.__setattr__(self, "subagent_type", self.subagent_type.strip().casefold())


@dataclass(frozen=True, slots=True)
class SubagentResult:
    """Terminal child result returned to the parent task tool."""

    child_run_id: str
    status: SubagentTerminalStatus
    result: str = ""
    result_ref: str = ""
    error_code: str = ""
    evidence_refs: tuple[str, ...] = ()
    token_usage: int = 0
    model_calls: int = 0
    tool_count: int = 0

    def __post_init__(self) -> None:
        if not self.child_run_id.strip():
            raise ValueError("child_run_id must not be empty")
        if self.result_ref and not self.result_ref.startswith("subagent://"):
            raise ValueError("result_ref must use the subagent scheme")
        if any(not item.strip() for item in self.evidence_refs):
            raise ValueError("evidence_refs must contain non-empty values")
        if self.token_usage < 0 or self.model_calls < 0 or self.tool_count < 0:
            raise ValueError("subagent usage counters must be non-negative")
        object.__setattr__(self, "evidence_refs", tuple(dict.fromkeys(self.evidence_refs)))


class SubagentExecutorPort(Protocol):
    """Application-owned child runtime used by the neutral task tool."""

    def execute(
        self,
        request: SubagentRequest,
        progress: SubagentProgressSink | None = None,
    ) -> Awaitable[SubagentResult]: ...

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
    "SubagentProfile",
    "SubagentProgressSink",
    "SubagentRequest",
    "SubagentResult",
    "SubagentTerminalStatus",
    "SubagentToolConfig",
    "derive_child_run_id",
]
