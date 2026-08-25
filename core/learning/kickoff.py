"""Durable acceptance and replay contract for one learning kickoff message."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from core.learning.activation import LearningActivationError, LearningActivationRecord
from core.learning.errors import LearningFailureCode
from core.learning.tasks import LearningTask

LearningKickoffReceiptStatus = Literal["dispatching", "accepted"]


LearningKickoffErrorCode = LearningFailureCode


LearningKickoffStage = Literal["intent", "journal", "accepted"]


@dataclass(frozen=True, slots=True)
class LearningKickoffDispatchRecord:
    """Internal receipt; raw idempotency keys never enter the browser response."""

    version: int
    owner_id: str
    workspace_id: str
    task_id: str
    task_revision: int
    idempotency_key: str
    activation_idempotency_key_hash: str
    kickoff_idempotency_key_hash: str
    dispatch_id: str
    session_id: str
    message_id: str
    acceptance_run_id: str
    turn_run_id: str
    content_hash: str
    receipt_status: LearningKickoffReceiptStatus
    stage: LearningKickoffStage
    created_at: str
    updated_at: str
    accepted_at: str | None


class LearningKickoffError(RuntimeError):
    """Stable public failure code for kickoff acceptance and replay."""

    def __init__(self, message: str, *, code: str | LearningKickoffErrorCode) -> None:
        super().__init__(message)
        self.code = LearningKickoffErrorCode(code)


class LearningKickoffRepositoryPort(Protocol):
    def begin_kickoff(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> tuple[LearningTask, LearningActivationRecord, LearningKickoffDispatchRecord]: ...

    def save_kickoff(
        self,
        record: LearningKickoffDispatchRecord,
        *,
        stage: LearningKickoffStage,
        receipt_status: LearningKickoffReceiptStatus,
    ) -> LearningKickoffDispatchRecord: ...

    def kickoff(
        self, *, owner_id: str, workspace_id: str, task_id: str
    ) -> LearningKickoffDispatchRecord: ...

    def accepted_kickoff_for_session(
        self, *, owner_id: str, workspace_id: str, session_id: str
    ) -> LearningKickoffDispatchRecord | None: ...

    def get(self, *, owner_id: str, workspace_id: str, task_id: str) -> LearningTask: ...

    def activation(
        self, *, owner_id: str, workspace_id: str, task_id: str
    ) -> LearningActivationRecord: ...


class LearningKickoffResources(Protocol):
    def ensure_journal_acceptance(
        self,
        *,
        record: LearningKickoffDispatchRecord,
    ) -> None: ...


FailureInjector = Callable[[str, LearningKickoffDispatchRecord], None]


class LearningKickoffService:
    """Accept one canonical kickoff through a replayable cross-store state machine."""

    def __init__(
        self,
        repository: LearningKickoffRepositoryPort,
        resources: LearningKickoffResources,
        *,
        failure_injector: FailureInjector | None = None,
    ) -> None:
        self.repository = repository
        self.resources = resources
        self.failure_injector = failure_injector

    def dispatch(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> LearningKickoffDispatchRecord:
        try:
            _, _, record = self.repository.begin_kickoff(
                owner_id=owner_id,
                workspace_id=workspace_id,
                task_id=task_id,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
            if record.receipt_status == "accepted":
                return record
            self._inject("after_intent", record)
            self.resources.ensure_journal_acceptance(record=record)
            record = self.repository.save_kickoff(
                record,
                stage="journal",
                receipt_status="dispatching",
            )
            self._inject("after_journal", record)
            record = self.repository.save_kickoff(
                record,
                stage="accepted",
                receipt_status="accepted",
            )
            self._inject("after_accepted", record)
            return record
        except LearningKickoffError:
            raise
        except Exception as exc:
            raise LearningKickoffError(
                "learning kickoff dispatch failed",
                code=LearningKickoffErrorCode.DISPATCH_FAILED,
            ) from exc

    def get(
        self, *, owner_id: str, workspace_id: str, task_id: str
    ) -> LearningKickoffDispatchRecord:
        record = self.repository.kickoff(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=task_id,
        )
        task = self._canonical_task(
            owner_id=owner_id, workspace_id=workspace_id, task_id=record.task_id
        )
        activation = self._active_activation(
            owner_id=owner_id, workspace_id=workspace_id, task_id=record.task_id
        )
        self._validate_active_binding(record=record, task=task, activation=activation)
        return record

    def accepted_for_session(
        self, *, owner_id: str, workspace_id: str, session_id: str
    ) -> tuple[LearningKickoffDispatchRecord, LearningTask] | None:
        record = self.repository.accepted_kickoff_for_session(
            owner_id=owner_id,
            workspace_id=workspace_id,
            session_id=session_id,
        )
        if record is None:
            return None
        task = self._canonical_task(
            owner_id=owner_id, workspace_id=workspace_id, task_id=record.task_id
        )
        activation = self._active_activation(
            owner_id=owner_id, workspace_id=workspace_id, task_id=record.task_id
        )
        self._validate_active_binding(record=record, task=task, activation=activation)
        return record, task

    @staticmethod
    def _validate_active_binding(
        *,
        record: LearningKickoffDispatchRecord,
        task: LearningTask,
        activation: LearningActivationRecord,
    ) -> None:
        if (
            task.status != "active"
            or task.task_revision != record.task_revision
            or activation.receipt_status != "active"
            or activation.stage != "active"
            or activation.task_id != record.task_id
            or activation.task_revision != record.task_revision
            or activation.session_id != record.session_id
            or _sha256(activation.idempotency_key) != record.activation_idempotency_key_hash
            or _sha256(task.topic) != record.content_hash
        ):
            raise LearningKickoffError(
                "learning kickoff canonical binding changed",
                code=LearningKickoffErrorCode.BINDING_CONFLICT,
            )

    def _active_activation(
        self, *, owner_id: str, workspace_id: str, task_id: str
    ) -> LearningActivationRecord:
        try:
            return self.repository.activation(
                owner_id=owner_id,
                workspace_id=workspace_id,
                task_id=task_id,
            )
        except LearningActivationError as exc:
            raise LearningKickoffError(
                "learning kickoff activation binding changed",
                code=LearningKickoffErrorCode.BINDING_CONFLICT,
            ) from exc

    def _canonical_task(self, *, owner_id: str, workspace_id: str, task_id: str) -> LearningTask:
        try:
            return self.repository.get(
                owner_id=owner_id,
                workspace_id=workspace_id,
                task_id=task_id,
            )
        except KeyError as exc:
            raise LearningKickoffError(
                "learning kickoff task binding changed",
                code=LearningKickoffErrorCode.BINDING_CONFLICT,
            ) from exc

    def _inject(self, point: str, record: LearningKickoffDispatchRecord) -> None:
        if self.failure_injector is not None:
            self.failure_injector(point, record)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


__all__ = [
    "FailureInjector",
    "LearningKickoffDispatchRecord",
    "LearningKickoffError",
    "LearningKickoffErrorCode",
    "LearningKickoffReceiptStatus",
    "LearningKickoffResources",
    "LearningKickoffService",
    "LearningKickoffStage",
]
