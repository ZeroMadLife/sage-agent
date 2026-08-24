"""Sage adapters that bind a learning activation to the shared Harness stores."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sage_harness import CapabilityRegistry

from core.coding.context import WorkspaceContext, now
from core.coding.memory import workspace_id_from_path
from core.coding.persistence import (
    CodingSessionStore,
    TurnPlanConflictError,
    TurnPlanCorruptionError,
    TurnPlanStore,
)
from core.coding.persistence.session_event_journal import (
    SessionEventJournal,
    SessionThreadGoalConflictError,
)
from core.coding.skills import SkillRegistry
from core.coding.tools.registry import build_tool_registry
from core.harness.capability_adapter import build_sage_capability_registry
from core.harness.thread_goal import ThreadGoalService
from core.harness.turn_context_plan import TurnContextPlan
from core.learning.activation import (
    LearningActivationError,
    LearningActivationRecord,
    LearningActivationTurnContextBinding,
)
from core.learning.tasks import LearningTask


class SageLearningActivationResources:
    """Create stable Session, Goal, and Plan resources without invoking a model."""

    def __init__(
        self,
        *,
        storage_root: Path,
        workspace_root: Path,
        runtime_profile: str,
        sandbox_provider: str,
        sandbox_image: str,
        knowledge_available: bool,
        web_search_available: bool,
        web_fetch_available: bool,
    ) -> None:
        self.storage_root = storage_root.resolve()
        self.workspace_root = workspace_root.resolve()
        self.runtime_profile = runtime_profile
        self.sandbox_provider = sandbox_provider
        self.sandbox_image = sandbox_image
        self.knowledge_available = knowledge_available
        self.web_search_available = web_search_available
        self.web_fetch_available = web_fetch_available

    def ensure_session(self, *, activation: LearningActivationRecord, task: LearningTask) -> None:
        """Create or validate one stable shared session, then make it visible again."""
        session_store = self._session_store()
        try:
            session = session_store.load(activation.session_id)
        except FileNotFoundError:
            session = {
                "id": activation.session_id,
                "workspace_root": str(self.workspace_root),
                "created_at": activation.created_at,
                "updated_at": activation.created_at,
                "history": [],
                "runtime_mode": {"mode": "default"},
                "runtime_profile": self.runtime_profile,
                "todos": {"next_id": 1, "items": []},
                "activated_tools": [],
                "permission_mode": "default",
                "sandbox_provider": self.sandbox_provider,
                "sandbox_image": self.sandbox_image,
                "archived": False,
                "session_kind": "learning",
                "learning_owner_id": activation.owner_id,
                "learning_task_id": task.task_id,
                "learning_task_revision": task.task_revision,
            }
            if activation.owner_id != "local":
                session["owner_user_id"] = activation.owner_id
            session_store.save(session)
            return
        self._validate_session(session, activation)
        if session.get("archived") is True:
            session["archived"] = False
            session["updated_at"] = now()
            session_store.save(session)

    def ensure_thread_goal(
        self, *, activation: LearningActivationRecord, task: LearningTask
    ) -> int:
        journal = SessionEventJournal(self.storage_root, activation.session_id)
        service = ThreadGoalService(journal)
        current = service.get()
        if current is not None:
            return self._validate_goal(current, activation)
        binding = {
            "workspace_id": workspace_id_from_path(self.workspace_root),
            "goal_id": activation.learning_goal_ref.goal_id,
            "goal_revision": activation.learning_goal_ref.goal_revision,
            "capabilities": [
                {
                    "capability_id": "task-understanding",
                    "label": "学习目标理解与证据规划",
                    "weight": 1.0,
                    "required": True,
                    "criterion_indexes": [0],
                }
            ],
        }
        goal: dict[str, object] | None
        try:
            goal = service.upsert(
                description=f"学习任务：{task.topic}",
                completion_criteria=[task.desired_outcome or "形成可验证的学习目标和下一步计划"],
                expected_revision=0,
                learning_goal=binding,
            )
        except SessionThreadGoalConflictError:
            goal = service.get()
            if goal is None:
                raise
        return self._validate_goal(goal, activation)

    def ensure_turn_context_plan(
        self,
        *,
        activation: LearningActivationRecord,
        task: LearningTask,
        thread_goal_revision: int,
    ) -> LearningActivationTurnContextBinding:
        registry = self._registry()
        allowed = self._allowed_capabilities(task, registry)
        capability_revision = _capability_revision(
            catalog_revision=registry.revision,
            task=task,
            allowed=allowed,
        )
        plan = TurnContextPlan.create(
            plan_id=activation.turn_context_plan_id,
            session_id=activation.session_id,
            run_id=activation.kickoff_run_id,
            owner_fingerprint="owner:"
            + hashlib.sha256(activation.owner_id.encode()).hexdigest()[:32],
            workspace_id=workspace_id_from_path(self.workspace_root),
            surface="coding",
            created_at=activation.created_at,
            admission={
                "task_kind": "learning",
                "task_id": task.task_id,
                "task_revision": task.task_revision,
                "confirmation_fingerprint": _digest(activation.idempotency_key),
                "thread_goal_revision": thread_goal_revision,
            },
            prompt={
                "static_policy": {
                    "template_id": "sage-learning-kickoff-v1",
                    "revision": "2026-08-13.1",
                    "rendered_content": (
                        "This is a confirmed learning task. Only the frozen allowed-capability "
                        "set may be used; plans are not mastery evidence."
                    ),
                },
                "dynamic_authority": {
                    "task_kind": "learning",
                    "capability_revision": capability_revision,
                },
            },
            context_refs={
                "learning_task_ref": {
                    "task_id": task.task_id,
                    "task_revision": task.task_revision,
                },
                "learning_goal_ref": {
                    "goal_id": activation.learning_goal_ref.goal_id,
                    "goal_revision": activation.learning_goal_ref.goal_revision,
                },
            },
            retrieval={
                "knowledge_policy": task.source_policy.knowledge,
                "web_policy": task.source_policy.web,
                "domains": list(task.source_policy.domains),
                "freshness": task.source_policy.freshness,
            },
            tools={
                "catalog_revision": registry.revision,
                "capability_revision": capability_revision,
                "allowed_capabilities": list(allowed),
            },
            execution={
                "task_kind": "learning",
                "authority": "allowed_capability_set",
                "runtime_profile": self.runtime_profile,
                "read_only": True,
            },
            resume={
                "checkpoint_thread_id": activation.session_id,
                "checkpoint_namespace": "",
                "recovery_policy": "fail_closed",
                "task_revision": task.task_revision,
                "capability_revision": capability_revision,
            },
        )
        try:
            stored = TurnPlanStore(self.storage_root, activation.session_id).put_if_absent(plan)
        except (TurnPlanConflictError, TurnPlanCorruptionError) as exc:
            raise LearningActivationError(
                "learning activation plan binding conflict",
                code="learning_activation_plan_conflict",
            ) from exc
        if stored.plan_id != activation.turn_context_plan_id:
            raise LearningActivationError(
                "learning activation plan identity conflict",
                code="learning_activation_plan_conflict",
            )
        return LearningActivationTurnContextBinding(
            turn_context_plan_hash=stored.plan_hash,
            catalog_revision=registry.revision,
            capability_revision=capability_revision,
            allowed_capabilities=allowed,
        )

    def archive_session(self, *, activation: LearningActivationRecord) -> None:
        session_store = self._session_store()
        try:
            session = session_store.load(activation.session_id)
        except FileNotFoundError:
            return
        self._validate_session(session, activation)
        session_store.update_metadata(activation.session_id, archived=True)

    def _session_store(self) -> CodingSessionStore:
        """Delay filesystem creation until an activation actually needs a session."""
        return CodingSessionStore(self.storage_root / "sessions")

    def _registry(self) -> CapabilityRegistry:
        workspace = WorkspaceContext(self.workspace_root)
        return build_sage_capability_registry(
            tools=build_tool_registry(workspace),
            skills=SkillRegistry(root=self.workspace_root).list(),
            web_search_available=self.web_search_available,
            web_fetch_available=self.web_fetch_available,
            research_subagent_available=(self.knowledge_available and self.web_search_available),
            practice_subagent_available=True,
        )

    def _allowed_capabilities(
        self,
        task: LearningTask,
        registry: CapabilityRegistry,
    ) -> tuple[str, ...]:
        candidates: list[str] = []
        if task.source_policy.knowledge != "disabled":
            candidates.append("local:knowledge_search")
        if task.source_policy.web != "forbidden":
            if self.web_search_available:
                candidates.append("web:search")
            if self.web_fetch_available:
                candidates.append("web:fetch")
            research = registry.get("subagent:research")
            if research is not None and research.availability == "available":
                candidates.append("subagent:research")
        allowed = tuple(sorted(set(candidates)))
        if any(registry.get(item) is None for item in allowed):
            raise LearningActivationError(
                "learning activation selected an unknown capability",
                code="learning_activation_capability_conflict",
            )
        return allowed

    def _validate_session(
        self, session: dict[str, object], activation: LearningActivationRecord
    ) -> None:
        expected = {
            "id": activation.session_id,
            "workspace_root": str(self.workspace_root),
            "session_kind": "learning",
            "learning_owner_id": activation.owner_id,
            "learning_task_id": activation.task_id,
            "learning_task_revision": activation.task_revision,
        }
        persisted_owner = str(session.get("owner_user_id", "")).strip() or None
        expected_owner = activation.owner_id if activation.owner_id != "local" else None
        if (
            any(session.get(key) != value for key, value in expected.items())
            or persisted_owner != expected_owner
        ):
            raise LearningActivationError(
                "learning activation session binding conflict",
                code="learning_activation_session_conflict",
            )

    def _validate_goal(self, goal: dict[str, object], activation: LearningActivationRecord) -> int:
        binding = goal.get("learning_goal")
        if not isinstance(binding, dict) or (
            binding.get("goal_id") != activation.learning_goal_ref.goal_id
            or binding.get("goal_revision") != activation.learning_goal_ref.goal_revision
            or binding.get("workspace_id") != workspace_id_from_path(self.workspace_root)
        ):
            raise LearningActivationError(
                "learning activation goal binding conflict",
                code="learning_activation_goal_conflict",
            )
        revision = goal.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise LearningActivationError(
                "learning activation goal revision is invalid",
                code="learning_activation_goal_conflict",
            )
        return revision


def _capability_revision(
    *, catalog_revision: str, task: LearningTask, allowed: tuple[str, ...]
) -> str:
    payload = {
        "catalog_revision": catalog_revision,
        "task_id": task.task_id,
        "task_revision": task.task_revision,
        "source_policy": {
            "knowledge": task.source_policy.knowledge,
            "web": task.source_policy.web,
            "domains": list(task.source_policy.domains),
            "freshness": task.source_policy.freshness,
        },
        "allowed_capabilities": list(allowed),
    }
    return "lcap_" + hashlib.sha256(_canonical_json(payload).encode()).hexdigest()[:32]


def _digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ["SageLearningActivationResources"]
