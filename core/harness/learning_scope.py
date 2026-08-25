"""Active LearningTask 的首轮只读能力投影与执行前门禁。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from sage_harness import HarnessRunContext, SageThreadState

from core.coding.skills import SkillLifecycleSnapshot
from core.learning.activation import (
    LearningActivationRecord,
    LearningActivationRepositoryPort,
    LearningActivationResources,
)
from core.learning.tasks import LearningTask

LearningWebPolicy = Literal["allowed_when_insufficient", "forbidden"]
LearningKnowledgePolicy = Literal["preferred", "required", "disabled"]
LearningSourceFreshness = Literal["all", "current"]

_SPECIAL_CAPABILITIES = {
    "knowledge_search": "local:knowledge_search",
    "search_web": "web:search",
    "fetch_web": "web:fetch",
}


class LearningScopeConflict(RuntimeError):
    """Stable conflict raised before a model or real tool receives unsafe scope."""

    status_code = 409

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class LearningReadonlyScope:
    """Browser-safe authority frozen by one active L0 receipt."""

    task_id: str
    task_revision: int
    session_id: str
    owner_id: str
    workspace_id: str
    turn_context_plan_id: str
    turn_context_plan_hash: str
    catalog_revision: str
    capability_revision: str
    allowed_capabilities: tuple[str, ...]
    source_policy_revision: str
    knowledge_policy: LearningKnowledgePolicy
    web_policy: LearningWebPolicy
    domains: tuple[str, ...]
    freshness: LearningSourceFreshness

    def __post_init__(self) -> None:
        for name in (
            "task_id",
            "session_id",
            "owner_id",
            "workspace_id",
            "turn_context_plan_id",
            "turn_context_plan_hash",
            "catalog_revision",
            "capability_revision",
            "source_policy_revision",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} must not be empty")
        if isinstance(self.task_revision, bool) or self.task_revision < 1:
            raise ValueError("task_revision must be positive")
        if tuple(sorted(set(self.allowed_capabilities))) != self.allowed_capabilities:
            raise ValueError("allowed_capabilities must be sorted and unique")
        if self.web_policy not in {"allowed_when_insufficient", "forbidden"}:
            raise ValueError("unsupported learning web policy")
        if self.knowledge_policy not in {"preferred", "required", "disabled"}:
            raise ValueError("unsupported learning knowledge policy")
        if self.freshness not in {"all", "current"}:
            raise ValueError("unsupported learning source freshness")
        if self.web_policy == "forbidden" and any(
            capability.startswith("web:") or capability == "subagent:research"
            for capability in self.allowed_capabilities
        ):
            raise LearningScopeConflict("learning_scope_web_forbidden")

    def capability_for_tool(
        self,
        tool_name: str,
        args: Mapping[str, object] | None = None,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> str | None:
        """Resolve one real call to the same stable ID used by the frozen receipt."""
        if tool_name == "task":
            subagent_type = str((args or {}).get("subagent_type", "")).strip().casefold()
            return f"subagent:{subagent_type}" if subagent_type else None
        configured = str((metadata or {}).get("capability_id", "")).strip()
        if configured:
            return configured
        return _SPECIAL_CAPABILITIES.get(tool_name, f"local:{tool_name}")

    def allows_tool(
        self,
        tool_name: str,
        args: Mapping[str, object] | None = None,
        *,
        metadata: Mapping[str, object] | None = None,
    ) -> bool:
        """Return true only for an exact allowed capability and read-only MCP metadata."""
        if tool_name == "tool_search":
            return any(
                capability.startswith(("mcp:", "skill:"))
                or (self.knowledge_policy == "disabled" and capability.startswith("web:"))
                for capability in self.allowed_capabilities
            )
        capability_id = self.capability_for_tool(tool_name, args, metadata=metadata)
        if capability_id not in self.allowed_capabilities:
            return False
        if (
            capability_id is not None
            and capability_id.startswith("web:")
            and self.web_policy == "allowed_when_insufficient"
            and self.knowledge_policy != "disabled"
        ):
            # L1 has no durable Knowledge-sufficiency receipt. Until L3 can
            # prove a source gap, direct Web remains hidden and fail closed.
            return False
        if capability_id is not None and capability_id.startswith("mcp:"):
            return (metadata or {}).get("read_only") is True
        return True

    def constrain_retrieval_sources(
        self,
        selected: tuple[str, ...] | frozenset[str],
        *,
        available: tuple[str, ...] | frozenset[str],
    ) -> tuple[str, ...]:
        """Keep the public gate receipt aligned with executable L1 read authority."""
        requested = set(selected)
        available_sources = set(available)
        constrained: set[str] = set()
        if "local:memory_read" in self.allowed_capabilities:
            constrained.update(requested.intersection({"semantic_memory", "episodic_memory"}))

        retrieval_requested = bool(requested.intersection({"knowledge", "web"}))
        knowledge_ready = (
            self.knowledge_policy != "disabled"
            and "local:knowledge_search" in self.allowed_capabilities
            and "knowledge" in available_sources
        )
        if retrieval_requested and knowledge_ready:
            # L1 has no durable sufficiency receipt, so Web intent is routed to
            # Knowledge first instead of advertising an unusable Web source.
            constrained.add("knowledge")

        direct_web_ready = (
            self.knowledge_policy == "disabled"
            and self.web_policy != "forbidden"
            and "web" in requested
            and "web" in available_sources
            and any(capability.startswith("web:") for capability in self.allowed_capabilities)
        )
        if direct_web_ready:
            constrained.add("web")
        return tuple(
            source
            for source in ("semantic_memory", "episodic_memory", "knowledge", "web")
            if source in constrained
        )

    def assert_current(self, current: LearningReadonlyScope) -> None:
        """Compare all authorities before a handler may run, with stable drift codes."""
        if (
            current.task_id != self.task_id
            or current.task_revision != self.task_revision
            or current.session_id != self.session_id
            or current.owner_id != self.owner_id
            or current.workspace_id != self.workspace_id
        ):
            raise LearningScopeConflict("learning_scope_task_mismatch")
        if (
            current.turn_context_plan_id != self.turn_context_plan_id
            or current.turn_context_plan_hash != self.turn_context_plan_hash
        ):
            raise LearningScopeConflict("learning_scope_plan_mismatch")
        if current.catalog_revision != self.catalog_revision:
            raise LearningScopeConflict("learning_scope_catalog_revision_mismatch")
        if current.capability_revision != self.capability_revision:
            raise LearningScopeConflict("learning_scope_capability_revision_mismatch")
        if current.allowed_capabilities != self.allowed_capabilities:
            raise LearningScopeConflict("learning_scope_allowlist_mismatch")
        if (
            current.source_policy_revision != self.source_policy_revision
            or current.knowledge_policy != self.knowledge_policy
            or current.web_policy != self.web_policy
            or current.domains != self.domains
            or current.freshness != self.freshness
        ):
            raise LearningScopeConflict("learning_scope_source_policy_mismatch")

    def assert_required_sources(self, selected_sources: frozenset[str]) -> None:
        """Do not let a required Knowledge route degrade into an ungrounded model call."""
        if self.knowledge_policy == "required" and "knowledge" not in selected_sources:
            raise LearningScopeConflict("learning_scope_source_gap")

    def skill_allowed_tool_names(
        self,
        lifecycle: SkillLifecycleSnapshot,
        *,
        skill_capability_id: str | None,
    ) -> frozenset[str]:
        """An allowed Skill remains inert until its exact lifecycle is active."""
        if not lifecycle.activation_ref:
            return frozenset()
        if skill_capability_id is None or skill_capability_id not in self.allowed_capabilities:
            raise LearningScopeConflict("learning_scope_skill_not_activated")
        return frozenset(lifecycle.allowed_tools)

    @classmethod
    def from_canonical(
        cls,
        *,
        task: LearningTask,
        activation: LearningActivationRecord,
    ) -> LearningReadonlyScope:
        """Create a scope only from an active L0 task/receipt identity."""
        if task.status != "active" or activation.receipt_status != "active":
            raise LearningScopeConflict("learning_scope_not_active")
        if (
            task.learning_plan_id is not None
            or task.learning_plan_hash is not None
            or task.dag_hash is not None
            or activation.learning_plan_id is not None
            or activation.learning_plan_hash is not None
            or activation.dag_hash is not None
        ):
            raise LearningScopeConflict("learning_scope_future_identity_present")
        if (
            task.task_id != activation.task_id
            or task.task_revision != activation.task_revision
            or task.workspace_id != activation.workspace_id
        ):
            raise LearningScopeConflict("learning_scope_task_mismatch")
        if not activation.turn_context_plan_hash:
            raise LearningScopeConflict("learning_scope_plan_missing")
        if not activation.catalog_revision or not activation.capability_revision:
            raise LearningScopeConflict("learning_scope_revision_missing")
        return cls(
            task_id=task.task_id,
            task_revision=task.task_revision,
            session_id=activation.session_id,
            owner_id=activation.owner_id,
            workspace_id=activation.workspace_id,
            turn_context_plan_id=activation.turn_context_plan_id,
            turn_context_plan_hash=activation.turn_context_plan_hash,
            catalog_revision=activation.catalog_revision,
            capability_revision=activation.capability_revision,
            allowed_capabilities=activation.allowed_capabilities,
            source_policy_revision=activation.source_policy_revision,
            knowledge_policy=activation.source_policy_snapshot.knowledge,
            web_policy=activation.source_policy_snapshot.web,
            domains=activation.source_policy_snapshot.domains,
            freshness=activation.source_policy_snapshot.freshness,
        )


class LearningReadonlyScopeResolver:
    """Reload L0 stores and return one execution-ready scope for a learning Session."""

    def __init__(
        self,
        repository: LearningActivationRepositoryPort,
        resources: LearningActivationResources,
    ) -> None:
        self.repository = repository
        self.resources = resources

    def resolve_runtime_session(
        self,
        session: Mapping[str, object],
        *,
        session_id: str | None = None,
        owner_id: str,
        workspace_id: str,
    ) -> LearningReadonlyScope | None:
        """Use the active repository binding before accepting any Session marker."""
        canonical_session_id = str(session_id or session.get("id", "")).strip()
        if not canonical_session_id:
            raise LearningScopeConflict("learning_scope_session_invalid")
        try:
            activation = self.repository.active_activation_for_session(
                owner_id=owner_id,
                workspace_id=workspace_id,
                session_id=canonical_session_id,
            )
        except Exception as exc:
            raise LearningScopeConflict("learning_scope_validation_failed") from exc
        if activation is None:
            if session.get("session_kind") == "learning":
                raise LearningScopeConflict("learning_scope_session_invalid")
            return None
        if (
            session.get("id") != activation.session_id
            or session.get("session_kind") != "learning"
            or session.get("learning_task_id") != activation.task_id
            or session.get("learning_owner_id") != activation.owner_id
            or session.get("learning_workspace_id") != activation.workspace_id
            or session.get("learning_task_revision") != activation.task_revision
        ):
            raise LearningScopeConflict("learning_scope_session_mismatch")
        scope = self.resolve(
            owner_id=activation.owner_id,
            workspace_id=activation.workspace_id,
            task_id=activation.task_id,
        )
        if (
            canonical_session_id != scope.session_id
            or owner_id != scope.owner_id
            or workspace_id != scope.workspace_id
        ):
            raise LearningScopeConflict("learning_scope_session_mismatch")
        return scope

    def resolve_active_owner_session(
        self,
        *,
        owner_id: str,
        session_id: str,
    ) -> LearningReadonlyScope | None:
        """Resolve canonical Learning authority before reading mutable Session JSON."""
        try:
            activation = self.repository.active_activation_for_owner_session(
                owner_id=owner_id,
                session_id=session_id,
            )
            if activation is None:
                return None
            scope = self.resolve(
                owner_id=activation.owner_id,
                workspace_id=activation.workspace_id,
                task_id=activation.task_id,
            )
        except LearningScopeConflict:
            raise
        except Exception as exc:
            raise LearningScopeConflict("learning_scope_validation_failed") from exc
        if scope.session_id != session_id or scope.owner_id != owner_id:
            raise LearningScopeConflict("learning_scope_session_mismatch")
        return scope

    def resolve(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
    ) -> LearningReadonlyScope:
        """Reload Task, receipt, Session and TurnContextPlan before granting scope."""
        try:
            task = self.repository.get(
                owner_id=owner_id,
                workspace_id=workspace_id,
                task_id=task_id,
            )
            activation = self.repository.activation(
                owner_id=owner_id,
                workspace_id=workspace_id,
                task_id=task_id,
            )
            scope = LearningReadonlyScope.from_canonical(task=task, activation=activation)
            self.resources.validate_resume(activation=activation, task=task)
            return scope
        except LearningScopeConflict:
            raise
        except Exception as exc:
            raise LearningScopeConflict("learning_scope_validation_failed") from exc

    def revalidate(self, scope: LearningReadonlyScope) -> LearningReadonlyScope:
        """Reload the exact owner/workspace/task binding before a tool handler."""
        return self.resolve(
            owner_id=scope.owner_id,
            workspace_id=scope.workspace_id,
            task_id=scope.task_id,
        )


class LearningScopeMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Hide forbidden schemas and fence every actual ToolNode dispatch."""

    state_schema = SageThreadState

    def __init__(
        self,
        scope: LearningReadonlyScope,
        *,
        revalidate: Callable[[], LearningReadonlyScope],
    ) -> None:
        super().__init__()
        self.scope = scope
        self._revalidate = revalidate

    def _filter_request(
        self,
        request: ModelRequest[HarnessRunContext],
    ) -> ModelRequest[HarnessRunContext]:
        self.scope.assert_current(self._revalidate())
        return request.override(
            tools=[
                tool
                for tool in request.tools
                if self.scope.allows_tool(
                    _tool_name(tool),
                    _model_tool_args(tool),
                    metadata=_tool_metadata(tool),
                )
            ]
        )

    def _blocked_message(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_name = str(request.tool_call.get("name") or "unknown")
        raw_args = request.tool_call.get("args")
        args = raw_args if isinstance(raw_args, Mapping) else {}
        metadata = _tool_metadata(request.tool)
        if not self.scope.allows_tool(tool_name, args, metadata=metadata):
            return self._denial(request, "learning_scope_tool_forbidden")
        try:
            self.scope.assert_current(self._revalidate())
        except LearningScopeConflict as exc:
            return self._denial(request, exc.code)
        except Exception:
            return self._denial(request, "learning_scope_validation_failed")
        return None

    def _denial(self, request: ToolCallRequest, reason_code: str) -> ToolMessage:
        return ToolMessage(
            content=reason_code,
            tool_call_id=str(request.tool_call.get("id") or "missing_tool_call_id"),
            name=str(request.tool_call.get("name") or "unknown"),
            status="error",
            additional_kwargs={
                "sage_learning_scope": {
                    "task_id": self.scope.task_id,
                    "status": "denied",
                    "reason_code": reason_code,
                }
            },
        )

    @override
    def wrap_model_call(
        self,
        request: ModelRequest[HarnessRunContext],
        handler: Callable[[ModelRequest[HarnessRunContext]], ModelCallResult],
    ) -> ModelCallResult:
        return handler(self._filter_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[HarnessRunContext],
        handler: Callable[[ModelRequest[HarnessRunContext]], Awaitable[ModelCallResult]],
    ) -> ModelCallResult:
        return await handler(self._filter_request(request))

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._blocked_message(request)
        return blocked if blocked is not None else handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._blocked_message(request)
        return blocked if blocked is not None else await handler(request)


def _tool_name(tool: BaseTool | dict[str, Any]) -> str:
    if isinstance(tool, BaseTool):
        return tool.name
    direct = tool.get("name")
    if isinstance(direct, str):
        return direct
    function = tool.get("function")
    return str(function.get("name") or "") if isinstance(function, Mapping) else ""


def _tool_metadata(tool: object) -> Mapping[str, object]:
    metadata = getattr(tool, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def _model_tool_args(tool: BaseTool | dict[str, Any]) -> Mapping[str, object]:
    # Model visibility for task is profile-level. Argument-level research/practice
    # separation is repeated in ``_blocked_message`` before the handler.
    return {"subagent_type": "research"} if _tool_name(tool) == "task" else {}


__all__ = [
    "LearningReadonlyScope",
    "LearningReadonlyScopeResolver",
    "LearningScopeConflict",
    "LearningScopeMiddleware",
]
