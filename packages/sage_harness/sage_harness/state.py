"""Durable thread state and reducers for the Sage harness."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal, NotRequired, TypedDict

from langchain.agents import AgentState

GoalStatus = Literal["pending", "in_progress", "succeeded", "failed", "cancelled"]
DelegationStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]
ApprovalStatus = Literal["pending", "approved", "rejected", "expired"]
TERMINAL_GOAL_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})
TERMINAL_DELEGATION_STATUSES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})
TERMINAL_APPROVAL_STATUSES: frozenset[str] = frozenset({"approved", "rejected", "expired"})
MAX_ARTIFACTS = 100
MAX_DELEGATIONS = 50
MAX_SKILL_CONTEXT = 8
MAX_MEMORY_REFS = 32


class ThreadDataState(TypedDict, total=False):
    """Paths owned by a host workspace binding."""

    workspace_path: str | None
    uploads_path: str | None
    outputs_path: str | None


class SandboxState(TypedDict):
    """Identity of the sandbox associated with the current thread."""

    sandbox_id: str | None


class ArtifactRef(TypedDict, total=False):
    """Small artifact reference; large contents stay in an Artifact Store."""

    artifact_id: str
    kind: str
    path: str
    label: str


class TodoItem(TypedDict, total=False):
    """User-visible task projection owned by the agent loop."""

    id: str
    title: str
    status: Literal["pending", "in_progress", "completed", "blocked", "cancelled"]


class GoalState(TypedDict, total=False):
    """A single durable objective whose terminal state cannot be downgraded."""

    goal_id: str
    description: str
    status: GoalStatus
    updated_at: str


class DelegationEntry(TypedDict, total=False):
    """One child-agent ledger entry, deduplicated by id."""

    id: str
    run_id: str
    description: str
    subagent_type: str
    status: DelegationStatus
    result_brief: str
    result_ref: str
    created_at: str


class SkillRef(TypedDict, total=False):
    """Reference to a loaded skill; skill bodies never enter checkpoint state."""

    name: str
    path: str
    description: str
    loaded_at: int


class MemoryRef(TypedDict, total=False):
    """Reference to durable memory; full memory content stays in its store."""

    memory_id: str
    topic: str
    summary: str
    revision: str


class ApprovalContext(TypedDict, total=False):
    """Pending or resolved approval bound to one exact tool call."""

    request_id: str
    tool_call_id: str
    args_digest: str
    status: ApprovalStatus
    decided_by: str


def merge_thread_data(
    existing: ThreadDataState | None,
    new: ThreadDataState | None,
) -> ThreadDataState | None:
    """Merge paths while rejecting a thread workspace identity change."""
    if new is None:
        return existing
    if existing is None:
        return new
    old_workspace = existing.get("workspace_path")
    new_workspace = new.get("workspace_path")
    if old_workspace and new_workspace and old_workspace != new_workspace:
        raise ValueError(f"Conflicting workspace paths: {old_workspace!r} != {new_workspace!r}")
    return {**existing, **new}


def merge_sandbox(
    existing: SandboxState | None,
    new: SandboxState | None,
) -> SandboxState | None:
    """Merge idempotent sandbox initialization and fail closed on conflicts."""
    if new is None:
        return existing
    if existing is None:
        return new
    old_id = existing.get("sandbox_id")
    new_id = new.get("sandbox_id")
    if old_id is None:
        return new
    if new_id is None:
        return existing
    if old_id == new_id:
        return existing
    raise ValueError(f"Conflicting sandbox ids: {old_id!r} != {new_id!r}")


def merge_artifacts(
    existing: list[ArtifactRef] | None,
    new: list[ArtifactRef] | None,
) -> list[ArtifactRef]:
    """Deduplicate artifact references by stable id and cap checkpoint growth."""
    if new is None:
        return list(existing or [])[-MAX_ARTIFACTS:]
    if not new:
        return []

    by_id: dict[str, ArtifactRef] = {}
    order: list[str] = []
    for item in [*(existing or []), *new]:
        artifact_id = item.get("artifact_id")
        if not artifact_id:
            raise ValueError("Artifact references require a stable artifact_id")
        if artifact_id not in by_id:
            order.append(artifact_id)
        by_id[artifact_id] = item
    return [by_id[item_id] for item_id in order[-MAX_ARTIFACTS:]]


def merge_todos(
    existing: list[TodoItem] | None,
    new: list[TodoItem] | None,
) -> list[TodoItem] | None:
    """Preserve omitted todos while allowing an explicit empty-list clear."""
    if new is None:
        return existing
    return list(new)


def merge_goal(existing: GoalState | None, new: GoalState | None) -> GoalState | None:
    """Keep a terminal goal from being replaced by an in-progress update."""
    if new is None:
        return existing
    if existing is None:
        return new

    old_id = existing.get("goal_id")
    new_id = new.get("goal_id")
    if old_id and new_id and old_id != new_id:
        raise ValueError(f"Conflicting goal ids: {old_id!r} != {new_id!r}")

    old_status = existing.get("status")
    new_status = new.get("status")
    if old_status in TERMINAL_GOAL_STATUSES and new_status not in TERMINAL_GOAL_STATUSES:
        return existing
    if old_status in TERMINAL_GOAL_STATUSES and new_status in TERMINAL_GOAL_STATUSES and old_status != new_status:
        raise ValueError(f"Conflicting terminal goal statuses: {old_status!r} != {new_status!r}")
    return {**existing, **new}


def merge_delegations(
    existing: list[DelegationEntry] | None,
    new: list[DelegationEntry] | None,
) -> list[DelegationEntry]:
    """Merge child-agent updates and never downgrade terminal entries."""
    if new is None:
        return list(existing or [])[-MAX_DELEGATIONS:]
    if not new:
        return []

    by_id: dict[str, DelegationEntry] = {}
    order: list[str] = []
    for entry in [*(existing or []), *new]:
        entry_id = entry.get("id")
        status = entry.get("status")
        if not entry_id or not status:
            raise ValueError("Delegation entries require id and status")
        previous = by_id.get(entry_id)
        if previous and previous.get("status") in TERMINAL_DELEGATION_STATUSES and status not in TERMINAL_DELEGATION_STATUSES:
            continue
        if (
            previous
            and previous.get("status") in TERMINAL_DELEGATION_STATUSES
            and status in TERMINAL_DELEGATION_STATUSES
            and previous.get("status") != status
        ):
            raise ValueError(
                f"Conflicting terminal delegation statuses: {previous.get('status')!r} != {status!r}"
            )
        if entry_id not in by_id:
            order.append(entry_id)
        elif previous and previous.get("created_at") and not entry.get("created_at"):
            entry = {**entry, "created_at": previous["created_at"]}
        by_id[entry_id] = entry
    return [by_id[entry_id] for entry_id in order[-MAX_DELEGATIONS:]]


def _normalize_skill(entry: Mapping[str, object]) -> SkillRef:
    """Persist only a bounded skill reference, never the full skill body."""
    path = str(entry.get("path") or "")
    if not path:
        raise ValueError("Skill references require a path")
    description = entry.get("description")
    loaded_at = entry.get("loaded_at")
    return {
        "name": str(entry.get("name") or ""),
        "path": path,
        "description": " ".join(description.split())[:500] if isinstance(description, str) else "",
        "loaded_at": loaded_at if isinstance(loaded_at, int) else 0,
    }


def merge_skill_context(
    existing: list[SkillRef] | None,
    new: list[SkillRef] | None,
) -> list[SkillRef]:
    """Deduplicate skill refs by path and keep only the most recent eight."""
    if new is None:
        normalized_existing = [_normalize_skill(item) for item in existing or []]
        return normalized_existing[-MAX_SKILL_CONTEXT:]
    if not new:
        return []

    by_path: dict[str, SkillRef] = {}
    order: list[str] = []
    for item in [*(existing or []), *new]:
        normalized_item = _normalize_skill(item)
        path = normalized_item["path"]
        if path in by_path:
            order.remove(path)
        order.append(path)
        by_path[path] = normalized_item
    return [by_path[path] for path in order[-MAX_SKILL_CONTEXT:]]


def merge_memory_refs(
    existing: list[MemoryRef] | None,
    new: list[MemoryRef] | None,
) -> list[MemoryRef]:
    """Deduplicate durable-memory references by provider-owned memory id."""
    if new is None:
        return list(existing or [])[-MAX_MEMORY_REFS:]
    if not new:
        return []

    by_id: dict[str, MemoryRef] = {}
    order: list[str] = []
    for item in [*(existing or []), *new]:
        memory_id = item.get("memory_id")
        if not memory_id:
            raise ValueError("Memory references require a stable memory_id")
        if memory_id not in by_id:
            order.append(memory_id)
        by_id[memory_id] = item
    return [by_id[memory_id] for memory_id in order[-MAX_MEMORY_REFS:]]


def merge_approval_context(
    existing: ApprovalContext | None,
    new: ApprovalContext | None,
) -> ApprovalContext | None:
    """Reject approval identity changes and preserve terminal decisions."""
    if new is None:
        return existing
    if existing is None:
        return new

    old_request = existing.get("request_id")
    new_request = new.get("request_id")
    if old_request and new_request and old_request != new_request:
        raise ValueError(f"Conflicting approval requests: {old_request!r} != {new_request!r}")

    for identity_field in ("tool_call_id", "args_digest"):
        old_identity = existing.get(identity_field)
        new_identity = new.get(identity_field)
        if old_identity and new_identity and old_identity != new_identity:
            raise ValueError(
                f"Conflicting approval {identity_field}: {old_identity!r} != {new_identity!r}"
            )

    old_status = existing.get("status")
    new_status = new.get("status")
    if old_status in TERMINAL_APPROVAL_STATUSES and new_status not in TERMINAL_APPROVAL_STATUSES:
        return existing
    if (
        old_status in TERMINAL_APPROVAL_STATUSES
        and new_status in TERMINAL_APPROVAL_STATUSES
        and old_status != new_status
    ):
        raise ValueError(f"Conflicting terminal approval statuses: {old_status!r} != {new_status!r}")
    return {**existing, **new}


class SageThreadState(AgentState):
    """Checkpoint-safe state shared by Sage's future harness surfaces."""

    thread_data: Annotated[NotRequired[ThreadDataState | None], merge_thread_data]
    surface_context: NotRequired[dict[str, object] | None]
    sandbox: Annotated[NotRequired[SandboxState | None], merge_sandbox]
    artifacts: Annotated[NotRequired[list[ArtifactRef] | None], merge_artifacts]
    todos: Annotated[NotRequired[list[TodoItem] | None], merge_todos]
    goal: Annotated[NotRequired[GoalState | None], merge_goal]
    delegations: Annotated[NotRequired[list[DelegationEntry] | None], merge_delegations]
    skill_context: Annotated[NotRequired[list[SkillRef] | None], merge_skill_context]
    memory_refs: Annotated[NotRequired[list[MemoryRef] | None], merge_memory_refs]
    approval_context: Annotated[NotRequired[ApprovalContext | None], merge_approval_context]
    summary_text: NotRequired[str | None]
    budget_run_id: NotRequired[str]
    run_token_usage: NotRequired[int]


__all__ = [
    "ApprovalContext",
    "ArtifactRef",
    "DelegationEntry",
    "DelegationStatus",
    "GoalState",
    "GoalStatus",
    "MemoryRef",
    "SageThreadState",
    "SandboxState",
    "SkillRef",
    "ThreadDataState",
    "TodoItem",
    "merge_approval_context",
    "merge_artifacts",
    "merge_delegations",
    "merge_goal",
    "merge_memory_refs",
    "merge_sandbox",
    "merge_skill_context",
    "merge_thread_data",
    "merge_todos",
]
