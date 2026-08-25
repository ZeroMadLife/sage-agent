"""Durable, idempotent bootstrap for one confirmed learning task."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from threading import RLock
from typing import Literal, Protocol

from core.learning.tasks import LearningSourcePolicy, LearningTask, source_policy_revision

LearningActivationStatus = Literal["activating", "activation_failed", "active"]
LearningActivationStage = Literal["intent", "session", "goal", "turn_context_plan", "active"]
LearningResumeValidationVersion = Literal["canonical_l0_v3", "legacy_l0_v2"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LearningGoalRef:
    goal_id: str
    goal_revision: str


@dataclass(frozen=True, slots=True)
class LearningActivationTurnContextBinding:
    """TurnContextPlan binding created during L0 bootstrap.

    L0 does not create a LearningPlan or Task DAG. The historical class name is
    retained for caller compatibility, while the field names carry the authority.
    """

    turn_context_plan_hash: str
    catalog_revision: str
    capability_revision: str
    allowed_capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LearningActivationRecord:
    """Internal activation intent plus the browser-safe receipt fields."""

    version: int
    owner_id: str
    workspace_id: str
    task_id: str
    task_revision: int
    idempotency_key: str
    session_id: str
    kickoff_run_id: str
    thread_goal_revision: int | None
    learning_goal_ref: LearningGoalRef
    learning_plan_id: str | None
    learning_plan_hash: str | None
    turn_context_plan_id: str
    turn_context_plan_hash: str | None
    dag_hash: str | None
    catalog_revision: str | None
    capability_revision: str | None
    allowed_capabilities: tuple[str, ...]
    source_policy_snapshot: LearningSourcePolicy
    source_policy_revision: str
    resume_validation_version: LearningResumeValidationVersion
    receipt_status: LearningActivationStatus
    stage: LearningActivationStage
    failure_code: str | None
    created_at: str
    updated_at: str
    completed_at: str | None

    @property
    def session_created(self) -> bool:
        return self.stage in {"session", "goal", "turn_context_plan", "active"}


@dataclass(frozen=True, slots=True)
class LearningLegacyActivationCandidate:
    """Unclaimed L0 row whose workspace may only be recovered from frozen resources."""

    owner_id: str
    task_id: str
    task_revision: int
    idempotency_key: str
    session_id: str
    kickoff_run_id: str
    turn_context_plan_id: str
    turn_context_plan_hash: str
    source_policy: LearningSourcePolicy
    catalog_revision: str
    capability_revision: str
    allowed_capabilities: tuple[str, ...]
    task_payload_json: str
    receipt_json: str


class LearningActivationError(RuntimeError):
    """A stable public failure code for activation control-flow errors."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class LearningActivationRepositoryPort(Protocol):
    def begin_activation(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> tuple[LearningTask, LearningActivationRecord]: ...

    def activation(
        self, *, owner_id: str, workspace_id: str, task_id: str
    ) -> LearningActivationRecord: ...

    def active_activation_for_session(
        self, *, owner_id: str, workspace_id: str, session_id: str
    ) -> LearningActivationRecord | None: ...

    def active_activation_for_owner_session(
        self, *, owner_id: str, session_id: str
    ) -> LearningActivationRecord | None: ...

    def save_activation(
        self,
        record: LearningActivationRecord,
        *,
        task_status: LearningActivationStatus,
        learning_goal_ref: LearningGoalRef | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> LearningActivationRecord: ...

    def archive_session_if_failed(
        self,
        record: LearningActivationRecord,
        *,
        archive_session: Callable[[], None],
    ) -> bool: ...

    def reconcilable_activations(self) -> tuple[LearningActivationRecord, ...]: ...

    def legacy_active_activations(self) -> tuple[LearningLegacyActivationCandidate, ...]: ...

    def backfill_legacy_workspace(
        self, candidate: LearningLegacyActivationCandidate, *, workspace_id: str
    ) -> bool: ...

    def block_unclaimed_legacy(self) -> int: ...

    def get(self, *, owner_id: str, workspace_id: str, task_id: str) -> LearningTask: ...


class LearningActivationResources(Protocol):
    def ensure_session(
        self, *, activation: LearningActivationRecord, task: LearningTask
    ) -> None: ...

    def ensure_thread_goal(
        self, *, activation: LearningActivationRecord, task: LearningTask
    ) -> int: ...

    def ensure_turn_context_plan(
        self,
        *,
        activation: LearningActivationRecord,
        task: LearningTask,
        thread_goal_revision: int,
    ) -> LearningActivationTurnContextBinding: ...

    def archive_session(self, *, activation: LearningActivationRecord) -> None: ...

    def validate_resume(
        self, *, activation: LearningActivationRecord, task: LearningTask
    ) -> None: ...

    def validate_legacy_workspace(self, *, candidate: LearningLegacyActivationCandidate) -> str: ...


FailureInjector = Callable[[str, LearningActivationRecord], None]


class LearningActivationService:
    """Make a cross-store bootstrap replayable instead of claiming one transaction."""

    def __init__(
        self,
        repository: LearningActivationRepositoryPort,
        resources: LearningActivationResources,
        *,
        failure_injector: FailureInjector | None = None,
    ) -> None:
        self.repository = repository
        self.resources = resources
        self.failure_injector = failure_injector
        self._bootstrap_lock = RLock()

    def activate(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> LearningActivationRecord:
        with self._bootstrap_lock:
            task, activation = self.repository.begin_activation(
                owner_id=owner_id,
                workspace_id=workspace_id,
                task_id=task_id,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
            if activation.receipt_status == "active":
                return activation
            return self._bootstrap(task, activation)

    def get(self, *, owner_id: str, workspace_id: str, task_id: str) -> LearningActivationRecord:
        return self.repository.activation(
            owner_id=owner_id, workspace_id=workspace_id, task_id=task_id
        )

    def resume(
        self, *, owner_id: str, workspace_id: str, task_id: str, expected_revision: int
    ) -> LearningActivationRecord:
        task = self.repository.get(owner_id=owner_id, workspace_id=workspace_id, task_id=task_id)
        if task.task_revision != expected_revision:
            raise LearningActivationError(
                "learning task revision conflict",
                code="learning_task_revision_conflict",
            )
        activation = self.get(owner_id=owner_id, workspace_id=workspace_id, task_id=task_id)
        if activation.receipt_status != "active":
            raise LearningActivationError(
                "learning task activation is not active",
                code="learning_activation_not_active",
            )
        if (
            task.learning_plan_id is not None
            or task.learning_plan_hash is not None
            or task.dag_hash is not None
            or activation.learning_plan_id is not None
            or activation.learning_plan_hash is not None
            or activation.dag_hash is not None
        ):
            raise LearningActivationError(
                "L0 receipt contains a future plan identity",
                code="learning_resume_validation_failed",
            )
        self.resources.validate_resume(activation=activation, task=task)
        return activation

    def reconcile(self) -> int:
        with self._bootstrap_lock:
            for candidate in self.repository.legacy_active_activations():
                try:
                    workspace_id = self.resources.validate_legacy_workspace(candidate=candidate)
                    self.repository.backfill_legacy_workspace(candidate, workspace_id=workspace_id)
                except Exception as exc:
                    logger.error(
                        "Learning legacy workspace migration skipped one record: %s",
                        type(exc).__name__,
                    )
            self.repository.block_unclaimed_legacy()
            repaired = 0
            for activation in self.repository.reconcilable_activations():
                try:
                    task = self.repository.get(
                        owner_id=activation.owner_id,
                        workspace_id=activation.workspace_id,
                        task_id=activation.task_id,
                    )
                    self._bootstrap(task, activation)
                except Exception as exc:
                    logger.error(
                        "Learning activation reconciliation skipped one record: %s",
                        type(exc).__name__,
                    )
                    continue
                repaired += 1
            return repaired

    def _bootstrap(
        self,
        task: LearningTask,
        activation: LearningActivationRecord,
    ) -> LearningActivationRecord:
        current = activation
        try:
            self._validate_l0_contract(task, current)
            self._inject("after_intent", current)
            self.resources.ensure_session(activation=current, task=task)
            current = self.repository.save_activation(
                replace(current, stage="session", receipt_status="activating", failure_code=None),
                task_status="activating",
            )
            self._inject("after_session", current)

            goal_revision = self.resources.ensure_thread_goal(activation=current, task=task)
            current = self.repository.save_activation(
                replace(
                    current,
                    stage="goal",
                    thread_goal_revision=goal_revision,
                    receipt_status="activating",
                    failure_code=None,
                ),
                task_status="activating",
            )
            self._inject("after_goal", current)

            turn_context_plan = self.resources.ensure_turn_context_plan(
                activation=current,
                task=task,
                thread_goal_revision=goal_revision,
            )
            current = self.repository.save_activation(
                replace(
                    current,
                    stage="turn_context_plan",
                    turn_context_plan_hash=turn_context_plan.turn_context_plan_hash,
                    catalog_revision=turn_context_plan.catalog_revision,
                    capability_revision=turn_context_plan.capability_revision,
                    allowed_capabilities=turn_context_plan.allowed_capabilities,
                    receipt_status="activating",
                    failure_code=None,
                ),
                task_status="activating",
            )
            self._inject("before_receipt", current)

            return self.repository.save_activation(
                replace(current, stage="active", receipt_status="active", failure_code=None),
                task_status="active",
                learning_goal_ref=current.learning_goal_ref,
                before_commit=lambda: self.resources.ensure_session(activation=current, task=task),
            )
        except LearningActivationError as exc:
            failed = self._save_failure(current, failure_code=exc.code)
            if failed.receipt_status == "active":
                return failed
            self._archive_failed_session(failed)
            raise
        except Exception as exc:
            failed = self._save_failure(current, failure_code=type(exc).__name__)
            if failed.receipt_status == "active":
                return failed
            self._archive_failed_session(failed)
            raise LearningActivationError(
                "learning activation failed",
                code="learning_activation_failed",
            ) from exc

    def _save_failure(
        self,
        activation: LearningActivationRecord,
        *,
        failure_code: str,
    ) -> LearningActivationRecord:
        return self.repository.save_activation(
            replace(
                activation,
                receipt_status="activation_failed",
                failure_code=failure_code,
            ),
            task_status="activation_failed",
        )

    def _validate_l0_contract(
        self, task: LearningTask, activation: LearningActivationRecord
    ) -> None:
        if (
            task.learning_plan_id is not None
            or task.learning_plan_hash is not None
            or task.dag_hash is not None
            or activation.learning_plan_id is not None
            or activation.learning_plan_hash is not None
            or activation.dag_hash is not None
        ):
            raise LearningActivationError(
                "L0 receipt contains a future plan identity",
                code="learning_activation_contract_conflict",
            )
        expected_revision = source_policy_revision(task.source_policy)
        if (
            activation.source_policy_snapshot != task.source_policy
            or activation.source_policy_revision != expected_revision
        ):
            raise LearningActivationError(
                "learning activation source policy drifted",
                code="learning_activation_source_policy_conflict",
            )

    def _archive_failed_session(self, activation: LearningActivationRecord) -> None:
        if activation.receipt_status != "activation_failed":
            return
        with suppress(Exception):
            self.repository.archive_session_if_failed(
                activation,
                archive_session=lambda: self.resources.archive_session(activation=activation),
            )

    def _inject(self, point: str, activation: LearningActivationRecord) -> None:
        if self.failure_injector is not None:
            self.failure_injector(point, activation)


__all__ = [
    "FailureInjector",
    "LearningActivationError",
    "LearningActivationRecord",
    "LearningActivationResources",
    "LearningActivationService",
    "LearningActivationStage",
    "LearningActivationStatus",
    "LearningActivationTurnContextBinding",
    "LearningGoalRef",
    "LearningLegacyActivationCandidate",
    "LearningResumeValidationVersion",
]
