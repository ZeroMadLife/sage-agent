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
    TurnPlanStoreError,
)
from core.coding.persistence.session_event_journal import (
    SessionEventJournal,
    SessionEventJournalError,
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
    LearningLegacyActivationCandidate,
)
from core.learning.kickoff import (
    LearningKickoffDispatchRecord,
    LearningKickoffError,
    LearningKickoffErrorCode,
)
from core.learning.tasks import LearningTask, source_policy_revision

_INTERNAL_LEARNING_READ_CAPABILITIES = frozenset({"local:evidence_read", "local:memory_read"})


class SageLearningKickoffResources:
    """Persist one browser-safe user acceptance into the shared Session Journal."""

    def __init__(self, *, storage_root: Path) -> None:
        self.storage_root = storage_root.resolve()

    def ensure_journal_acceptance(self, *, record: LearningKickoffDispatchRecord) -> None:
        journal = SessionEventJournal(self.storage_root, record.session_id)
        expected_payload = {
            "type": "learning_user_turn",
            "task_id": record.task_id,
            "run_id": record.turn_run_id,
            "status": "completed",
            "reason_code": "user_content_withheld",
            "dispatch_id": record.dispatch_id,
            "message_id": record.message_id,
        }
        current = journal.events_for_run(record.acceptance_run_id)
        if current:
            self._validate_acceptance(current, record=record, payload=expected_payload)
            return
        try:
            journal.append(
                run_id=record.acceptance_run_id,
                kind="user",
                status="completed",
                payload=expected_payload,
                event_id=record.message_id,
            )
        except SessionEventJournalError:
            current = journal.events_for_run(record.acceptance_run_id)
            self._validate_acceptance(current, record=record, payload=expected_payload)

    @staticmethod
    def _validate_acceptance(
        events: tuple[object, ...],
        *,
        record: LearningKickoffDispatchRecord,
        payload: dict[str, str],
    ) -> None:
        if len(events) != 1:
            raise LearningKickoffError(
                "learning kickoff journal event count changed",
                code=LearningKickoffErrorCode.JOURNAL_CONFLICT,
            )
        event = events[0]
        if (
            getattr(event, "event_id", None) != record.message_id
            or getattr(event, "kind", None) != "user"
            or getattr(event, "status", None) != "completed"
            or getattr(event, "payload", None) != payload
        ):
            raise LearningKickoffError(
                "learning kickoff journal binding changed",
                code=LearningKickoffErrorCode.JOURNAL_CONFLICT,
            )


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
        web_fetch_policy_aware: bool = False,
    ) -> None:
        self.storage_root = storage_root.resolve()
        self.workspace_root = workspace_root.resolve()
        self.runtime_profile = runtime_profile
        self.sandbox_provider = sandbox_provider
        self.sandbox_image = sandbox_image
        self.knowledge_available = knowledge_available
        self.web_search_available = web_search_available
        self.web_fetch_available = web_fetch_available
        self.web_fetch_policy_aware = web_fetch_policy_aware

    def ensure_session(self, *, activation: LearningActivationRecord, task: LearningTask) -> None:
        """Create or validate one stable shared session, then make it visible again."""
        self._validate_workspace(activation, task)
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
                "learning_workspace_id": activation.workspace_id,
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
        self._validate_workspace(activation, task)
        journal = SessionEventJournal(self.storage_root, activation.session_id)
        service = ThreadGoalService(journal)
        current = service.get()
        if current is not None:
            return self._validate_goal(current, activation)
        binding = {
            "workspace_id": activation.workspace_id,
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
        self._validate_workspace(activation, task)
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
            owner_fingerprint=_owner_fingerprint(activation.owner_id),
            workspace_id=activation.workspace_id,
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
                "source_policy_revision": activation.source_policy_revision,
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
                "catalog_revision": registry.revision,
                "source_policy_revision": activation.source_policy_revision,
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

    def validate_resume(self, *, activation: LearningActivationRecord, task: LearningTask) -> None:
        """Reload canonical runtime resources before a learning task may resume."""
        try:
            self._validate_workspace(activation, task)
            session = self._session_store().load(activation.session_id)
            self._validate_session(session, activation)
            if session.get("archived") is True:
                raise ValueError("learning resume session is archived")
            plan = TurnPlanStore(self.storage_root, activation.session_id).load_for_run(
                activation.kickoff_run_id
            )
            if plan is None:
                raise ValueError("learning resume turn context plan is missing")
            registry = self._registry()
            allowed = self._allowed_capabilities(task, registry)
            capability_revision = _capability_revision(
                catalog_revision=registry.revision,
                task=task,
                allowed=allowed,
            )
            expected_source_revision = source_policy_revision(task.source_policy)
            expected_source = {
                "knowledge": task.source_policy.knowledge,
                "web": task.source_policy.web,
                "domains": list(task.source_policy.domains),
                "freshness": task.source_policy.freshness,
            }
            frozen_source = {
                "knowledge": activation.source_policy_snapshot.knowledge,
                "web": activation.source_policy_snapshot.web,
                "domains": list(activation.source_policy_snapshot.domains),
                "freshness": activation.source_policy_snapshot.freshness,
            }
            payload = plan.to_payload()
            admission = _mapping(payload, "admission")
            refs = _mapping(payload, "context_refs")
            task_ref = _mapping(refs, "learning_task_ref")
            goal_ref = _mapping(refs, "learning_goal_ref")
            retrieval = _mapping(payload, "retrieval")
            tools = _mapping(payload, "tools")
            resume = _mapping(payload, "resume")
            common_drift = (
                plan.plan_id != activation.turn_context_plan_id
                or plan.plan_hash != activation.turn_context_plan_hash
                or plan.session_id != activation.session_id
                or plan.run_id != activation.kickoff_run_id
                or plan.owner_fingerprint != _owner_fingerprint(activation.owner_id)
                or plan.workspace_id != activation.workspace_id
                or activation.catalog_revision != registry.revision
                or activation.capability_revision != capability_revision
                or activation.allowed_capabilities != allowed
                or activation.source_policy_revision != expected_source_revision
                or frozen_source != expected_source
                or admission.get("task_id") != task.task_id
                or admission.get("task_revision") != task.task_revision
                or admission.get("thread_goal_revision") != activation.thread_goal_revision
                or task_ref != {"task_id": task.task_id, "task_revision": task.task_revision}
                or goal_ref
                != {
                    "goal_id": activation.learning_goal_ref.goal_id,
                    "goal_revision": activation.learning_goal_ref.goal_revision,
                }
                or tools.get("catalog_revision") != registry.revision
                or tools.get("capability_revision") != capability_revision
                or tools.get("allowed_capabilities") != list(allowed)
                or resume.get("task_revision") != task.task_revision
                or resume.get("capability_revision") != capability_revision
            )
            canonical_drift = (
                retrieval
                != {
                    "knowledge_policy": task.source_policy.knowledge,
                    "web_policy": task.source_policy.web,
                    "domains": list(task.source_policy.domains),
                    "freshness": task.source_policy.freshness,
                    "source_policy_revision": expected_source_revision,
                }
                or resume.get("catalog_revision") != registry.revision
                or resume.get("source_policy_revision") != expected_source_revision
            )
            legacy_drift = (
                retrieval
                != {
                    "knowledge_policy": task.source_policy.knowledge,
                    "web_policy": task.source_policy.web,
                    "domains": list(task.source_policy.domains),
                    "freshness": task.source_policy.freshness,
                }
                or "catalog_revision" in resume
                or "source_policy_revision" in resume
            )
            validation_drift = (
                canonical_drift
                if activation.resume_validation_version == "canonical_l0_v3"
                else legacy_drift
            )
            if common_drift or validation_drift:
                raise ValueError("learning resume binding drift")
        except (
            FileNotFoundError,
            OSError,
            ValueError,
            LearningActivationError,
            SessionEventJournalError,
            TurnPlanStoreError,
        ) as exc:
            raise LearningActivationError(
                "learning resume canonical validation failed",
                code="learning_resume_validation_failed",
            ) from exc

    def validate_legacy_workspace(self, *, candidate: LearningLegacyActivationCandidate) -> str:
        """Recover a legacy workspace only when Session and frozen Plan agree."""
        session_store = self._session_store()
        session = session_store.load(candidate.session_id)
        workspace_root = session.get("workspace_root")
        if not isinstance(workspace_root, str) or not workspace_root.strip():
            raise ValueError("legacy learning session workspace is missing")
        workspace_path = Path(workspace_root)
        if not workspace_path.is_absolute():
            raise ValueError("legacy learning session workspace must be absolute")
        workspace_id = workspace_id_from_path(workspace_path)
        expected_session = {
            "id": candidate.session_id,
            "session_kind": "learning",
            "learning_owner_id": candidate.owner_id,
            "learning_task_id": candidate.task_id,
            "learning_task_revision": candidate.task_revision,
        }
        persisted_owner = str(session.get("owner_user_id", "")).strip() or None
        expected_owner = candidate.owner_id if candidate.owner_id != "local" else None
        legacy_workspace = session.get("learning_workspace_id")
        if (
            any(session.get(key) != value for key, value in expected_session.items())
            or persisted_owner != expected_owner
            or legacy_workspace not in {None, workspace_id}
        ):
            raise ValueError("legacy learning session binding mismatch")
        plan = TurnPlanStore(self.storage_root, candidate.session_id).load_for_run(
            candidate.kickoff_run_id
        )
        if plan is None:
            raise ValueError("legacy learning turn context plan is missing")
        payload = plan.to_payload()
        admission = _mapping(payload, "admission")
        retrieval = _mapping(payload, "retrieval")
        tools = _mapping(payload, "tools")
        resume = _mapping(payload, "resume")
        expected_retrieval = {
            "knowledge_policy": candidate.source_policy.knowledge,
            "web_policy": candidate.source_policy.web,
            "domains": list(candidate.source_policy.domains),
            "freshness": candidate.source_policy.freshness,
        }
        if (
            plan.plan_id != candidate.turn_context_plan_id
            or plan.plan_hash != candidate.turn_context_plan_hash
            or plan.owner_fingerprint != _owner_fingerprint(candidate.owner_id)
            or plan.workspace_id != workspace_id
            or plan.session_id != candidate.session_id
            or plan.run_id != candidate.kickoff_run_id
            or admission.get("task_id") != candidate.task_id
            or admission.get("task_revision") != candidate.task_revision
            or any(retrieval.get(key) != value for key, value in expected_retrieval.items())
            or set(retrieval) != set(expected_retrieval)
            or tools.get("catalog_revision") != candidate.catalog_revision
            or tools.get("capability_revision") != candidate.capability_revision
            or tools.get("allowed_capabilities") != list(candidate.allowed_capabilities)
            or resume.get("task_revision") != candidate.task_revision
            or resume.get("capability_revision") != candidate.capability_revision
            or "catalog_revision" in resume
            or "source_policy_revision" in resume
        ):
            raise ValueError("legacy learning resource binding mismatch")
        if legacy_workspace is None:
            session["learning_workspace_id"] = workspace_id
            session_store.save(session)
        return workspace_id

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
            research_subagent_available=(self.knowledge_available or self.web_search_available),
            practice_subagent_available=True,
        )

    def _allowed_capabilities(
        self,
        task: LearningTask,
        registry: CapabilityRegistry,
    ) -> tuple[str, ...]:
        candidates: list[str] = ["local:evidence_read", "local:memory_read"]
        if task.source_policy.knowledge != "disabled":
            candidates.append("local:knowledge_search")
        if task.source_policy.web != "forbidden":
            if self.web_search_available:
                candidates.append("web:search")
            if self.web_fetch_available and (
                not task.source_policy.domains or self.web_fetch_policy_aware
            ):
                candidates.append("web:fetch")
            research = registry.get("subagent:research")
            if research is not None and research.availability == "available":
                candidates.append("subagent:research")
        allowed = tuple(sorted(set(candidates)))
        if any(
            item not in _INTERNAL_LEARNING_READ_CAPABILITIES and registry.get(item) is None
            for item in allowed
        ):
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
            "learning_workspace_id": activation.workspace_id,
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

    def _validate_workspace(self, activation: LearningActivationRecord, task: LearningTask) -> None:
        canonical = workspace_id_from_path(self.workspace_root)
        if activation.workspace_id != canonical or task.workspace_id != canonical:
            raise LearningActivationError(
                "learning activation workspace binding conflict",
                code="learning_activation_workspace_conflict",
            )

    def _validate_goal(self, goal: dict[str, object], activation: LearningActivationRecord) -> int:
        binding = goal.get("learning_goal")
        if not isinstance(binding, dict) or (
            binding.get("goal_id") != activation.learning_goal_ref.goal_id
            or binding.get("goal_revision") != activation.learning_goal_ref.goal_revision
            or binding.get("workspace_id") != activation.workspace_id
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


def _owner_fingerprint(owner_id: str) -> str:
    return "owner:" + hashlib.sha256(owner_id.encode()).hexdigest()[:32]


def _mapping(value: dict[str, object], key: str) -> dict[str, object]:
    nested = value.get(key)
    if not isinstance(nested, dict):
        raise ValueError(f"learning resume plan {key} is invalid")
    return nested


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


__all__ = ["SageLearningActivationResources"]
