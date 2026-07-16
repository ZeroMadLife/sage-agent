"""Application-neutral ports used by the Sage harness runtime."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

ToolExecutionStatus = Literal["succeeded", "failed", "rejected"]
ApprovalAction = Literal["once", "session", "reject"]


@dataclass(frozen=True, slots=True)
class HarnessEvent:
    """One source event awaiting persistence in an application timeline."""

    source_event_id: str
    thread_id: str
    run_id: str
    kind: str
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class HarnessCheckpoint:
    """Opaque graph state owned by a checkpointer implementation."""

    checkpoint_id: str
    thread_id: str
    run_id: str
    state: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """Normalized request passed from the agent loop to a tool adapter."""

    tool_call_id: str
    name: str
    arguments: Mapping[str, object] = field(default_factory=dict)
    risk_level: str = "low"
    permission_scope: str = ""


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    """Bounded tool result returned to the agent loop."""

    tool_call_id: str
    status: ToolExecutionStatus
    content: str
    artifact_refs: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """Server-owned approval request bound to exact tool arguments."""

    thread_id: str
    run_id: str
    call: ToolCallRequest
    args_digest: str


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """Decision returned by the host application's approval system."""

    action: ApprovalAction
    decided_by: str = ""


@dataclass(frozen=True, slots=True)
class KnowledgeEvidence:
    """Token-bounded evidence with a durable citation identity."""

    citation_id: str
    content: str
    source_revision: str
    score: float = 0.0
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MemoryReference:
    """Small durable-memory reference safe to assemble into context."""

    memory_id: str
    summary: str
    revision: str = ""
    metadata: Mapping[str, object] = field(default_factory=dict)


class HarnessEventSink(Protocol):
    """Persist harness events before an application streams them."""

    async def append(self, event: HarnessEvent) -> None: ...


class CheckpointPort(Protocol):
    """Load and store resumable agent state without exposing a concrete DB."""

    async def load_latest(self, thread_id: str) -> HarnessCheckpoint | None: ...

    async def save(self, checkpoint: HarnessCheckpoint) -> None: ...


class ToolExecutionPort(Protocol):
    """Execute a normalized tool call through the host policy boundary."""

    async def execute(self, call: ToolCallRequest) -> ToolExecutionResult: ...


class ApprovalPort(Protocol):
    """Resolve a pending high-risk operation through host-owned state."""

    async def request(self, request: ApprovalRequest) -> ApprovalDecision: ...


class KnowledgePort(Protocol):
    """Retrieve evidence without coupling the harness to a Knowledge store."""

    async def search(
        self,
        query: str,
        *,
        workspace_id: str,
        token_budget: int,
    ) -> Sequence[KnowledgeEvidence]: ...


class MemoryPort(Protocol):
    """Read context references and propose durable memory through the host."""

    async def load_context(
        self,
        thread_id: str,
        *,
        token_budget: int,
    ) -> Sequence[MemoryReference]: ...

    async def propose(
        self,
        thread_id: str,
        run_id: str,
        content: str,
    ) -> str: ...
