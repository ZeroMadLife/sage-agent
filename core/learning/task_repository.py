"""SQLite persistence and CAS updates for Sage learning-task contracts."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import cast
from uuid import uuid4

from core.learning.activation import (
    LearningActivationError,
    LearningActivationRecord,
    LearningActivationStage,
    LearningActivationStatus,
    LearningGoalRef,
    LearningLegacyActivationCandidate,
)
from core.learning.kickoff import (
    LearningKickoffDispatchRecord,
    LearningKickoffError,
    LearningKickoffReceiptStatus,
    LearningKickoffStage,
)
from core.learning.tasks import (
    LearningClarification,
    LearningClarificationQuestion,
    LearningLearnerProfile,
    LearningSourcePolicy,
    LearningTask,
    LearningTaskCreate,
    LearningTaskPatch,
    apply_task_patch,
    clarification_for,
    normalize_task_create,
    resolve_source_policy,
    risk_for_topic,
    source_policy_revision,
)

logger = logging.getLogger(__name__)

_TASK_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS learning_tasks (
    owner_id TEXT NOT NULL,
    workspace_id TEXT,
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL CHECK (task_revision >= 1),
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, workspace_id, task_id)
)
"""

_ACTIVATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS learning_task_activations (
    owner_id TEXT NOT NULL,
    workspace_id TEXT,
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL CHECK (task_revision >= 1),
    idempotency_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    kickoff_run_id TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY (owner_id, workspace_id, task_id, task_revision),
    UNIQUE (owner_id, workspace_id, idempotency_key)
)
"""

_KICKOFF_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS learning_task_kickoffs (
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL CHECK (task_revision >= 1),
    idempotency_key TEXT NOT NULL,
    activation_idempotency_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    accepted_at TEXT,
    PRIMARY KEY (owner_id, workspace_id, task_id, task_revision),
    UNIQUE (owner_id, workspace_id, idempotency_key),
    UNIQUE (owner_id, workspace_id, session_id)
)
"""


class LearningTaskError(RuntimeError):
    """Base error for learning-task persistence."""


class LearningTaskNotFoundError(LearningTaskError, KeyError):
    """The requested task is not visible to this owner."""


class LearningTaskConflictError(LearningTaskError):
    """A stale revision or invalid lifecycle mutation was attempted."""

    def __init__(self, message: str, *, current_revision: int) -> None:
        super().__init__(message)
        self.current_revision = current_revision


class LearningTaskRepository:
    """Persist task contracts independently from Mastery and runtime state."""

    def __init__(self, path: Path) -> None:
        expanded = path.expanduser()
        self.path = expanded if expanded.is_absolute() else Path.cwd() / expanded
        self._lock = Lock()
        self._initialized = False
        self._validate_path()

    def create(
        self, *, owner_id: str, workspace_id: str, request: LearningTaskCreate
    ) -> LearningTask:
        normalized = normalize_task_create(request)
        now = datetime.now(UTC).isoformat()
        policy = resolve_source_policy(normalized.topic, normalized.source_policy)
        risk_class, risk_notice = risk_for_topic(normalized.topic)
        task = LearningTask(
            version=1,
            workspace_id=_bounded_workspace(workspace_id),
            task_id=f"ltask_{uuid4().hex}",
            task_revision=1,
            template_id="freeform-learning-map-v1",
            topic=normalized.topic,
            desired_outcome=normalized.desired_outcome,
            learner_profile=LearningLearnerProfile(
                starting_level=normalized.starting_level,
                time_budget_minutes_per_week=normalized.time_budget_minutes_per_week,
                target_date=normalized.target_date,
            ),
            source_policy=policy,
            risk_class=risk_class,
            risk_notice=risk_notice,
            clarification=clarification_for(normalized),
            learning_plan_id=None,
            learning_plan_hash=None,
            dag_hash=None,
            learning_goal_ref=None,
            status="draft",
            created_at=now,
            updated_at=now,
        )
        owner = _bounded_owner(owner_id)
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO learning_tasks (
                    owner_id, workspace_id, task_id, task_revision, status, payload_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    owner,
                    task.workspace_id,
                    task.task_id,
                    task.task_revision,
                    task.status,
                    _encode(task),
                    task.created_at,
                    task.updated_at,
                ),
            )
            connection.commit()
        return task

    def get(self, *, owner_id: str, workspace_id: str, task_id: str) -> LearningTask:
        self._ensure_ready()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM learning_tasks WHERE owner_id = ? "
                "AND workspace_id = ? AND task_id = ?",
                (
                    _bounded_owner(owner_id),
                    _bounded_workspace(workspace_id),
                    _bounded_task_id(task_id),
                ),
            ).fetchone()
        if row is None:
            raise LearningTaskNotFoundError(task_id)
        return _decode_scoped_task(
            str(row["payload_json"]),
            workspace_id=_bounded_workspace(workspace_id),
            task_id=_bounded_task_id(task_id),
        )

    def list(self, *, owner_id: str, workspace_id: str) -> tuple[LearningTask, ...]:
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT task_id, payload_json FROM learning_tasks WHERE owner_id = ? "
                "AND workspace_id = ? ORDER BY updated_at DESC",
                (_bounded_owner(owner_id), _bounded_workspace(workspace_id)),
            ).fetchall()
        return tuple(
            _decode_scoped_task(
                str(row["payload_json"]),
                workspace_id=_bounded_workspace(workspace_id),
                task_id=str(row["task_id"]),
            )
            for row in rows
        )

    def update_draft(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        expected_revision: int,
        patch: LearningTaskPatch,
    ) -> LearningTask:
        if isinstance(expected_revision, bool) or expected_revision < 1:
            raise ValueError("expected_revision must be positive")
        owner = _bounded_owner(owner_id)
        workspace = _bounded_workspace(workspace_id)
        task_key = _bounded_task_id(task_id)
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM learning_tasks WHERE owner_id = ? "
                "AND workspace_id = ? AND task_id = ?",
                (owner, workspace, task_key),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise LearningTaskNotFoundError(task_key)
            current = _decode_scoped_task(
                str(row["payload_json"]), workspace_id=workspace, task_id=task_key
            )
            if current.task_revision != expected_revision:
                connection.rollback()
                raise LearningTaskConflictError(
                    f"learning task revision conflict: current revision is {current.task_revision}",
                    current_revision=current.task_revision,
                )
            if current.status not in {"draft", "activation_failed"}:
                connection.rollback()
                raise LearningTaskConflictError(
                    "only draft or failed-activation learning tasks can be edited",
                    current_revision=current.task_revision,
                )
            request = apply_task_patch(current, patch)
            policy = resolve_source_policy(request.topic, request.source_policy)
            risk_class, risk_notice = risk_for_topic(request.topic)
            updated = replace(
                current,
                task_revision=current.task_revision + 1,
                topic=request.topic,
                desired_outcome=request.desired_outcome,
                learner_profile=LearningLearnerProfile(
                    starting_level=request.starting_level,
                    time_budget_minutes_per_week=request.time_budget_minutes_per_week,
                    target_date=request.target_date,
                ),
                source_policy=policy,
                risk_class=risk_class,
                risk_notice=risk_notice,
                clarification=clarification_for(request),
                status="draft",
                updated_at=datetime.now(UTC).isoformat(),
            )
            cursor = connection.execute(
                """UPDATE learning_tasks
                   SET task_revision = ?, status = ?, payload_json = ?, updated_at = ?
                   WHERE owner_id = ? AND workspace_id = ? AND task_id = ?
                   AND task_revision = ?""",
                (
                    updated.task_revision,
                    updated.status,
                    _encode(updated),
                    updated.updated_at,
                    owner,
                    workspace,
                    task_key,
                    expected_revision,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise LearningTaskConflictError(
                    "learning task changed during update",
                    current_revision=current.task_revision,
                )
            if current.status == "activation_failed":
                connection.execute(
                    "UPDATE learning_task_activations SET status = 'superseded', updated_at = ? "
                    "WHERE owner_id = ? AND workspace_id = ? AND task_id = ? "
                    "AND task_revision = ? "
                    "AND status = 'activation_failed'",
                    (updated.updated_at, owner, workspace, task_key, expected_revision),
                )
            connection.commit()
        return updated

    def begin_activation(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> tuple[LearningTask, LearningActivationRecord]:
        """Persist one stable activation intent before creating external resources."""
        if isinstance(expected_revision, bool) or expected_revision < 1:
            raise ValueError("expected_revision must be positive")
        owner = _bounded_owner(owner_id)
        workspace = _bounded_workspace(workspace_id)
        task_key = _bounded_task_id(task_id)
        key = _bounded_idempotency_key(idempotency_key)
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT payload_json FROM learning_tasks WHERE owner_id = ? "
                    "AND workspace_id = ? AND task_id = ?",
                    (owner, workspace, task_key),
                ).fetchone()
                if row is None:
                    raise LearningTaskNotFoundError(task_key)
                task = _decode_scoped_task(
                    str(row["payload_json"]), workspace_id=workspace, task_id=task_key
                )
                if task.task_revision != expected_revision:
                    raise LearningTaskConflictError(
                        "learning task revision conflict",
                        current_revision=task.task_revision,
                    )
                if _has_future_l0_identity(task):
                    raise LearningActivationError(
                        "L0 task contains a future plan identity",
                        code="learning_activation_contract_conflict",
                    )
                existing = connection.execute(
                    "SELECT owner_id, workspace_id, task_id, task_revision, idempotency_key, session_id, "
                    "kickoff_run_id, status, stage, receipt_json FROM learning_task_activations "
                    "WHERE owner_id = ? AND workspace_id = ? AND task_id = ? "
                    "AND task_revision = ?",
                    (owner, workspace, task_key, expected_revision),
                ).fetchone()
                if existing is not None:
                    activation = _decode_activation_row(existing)
                    if activation.idempotency_key != key:
                        raise LearningActivationError(
                            "learning task revision already uses a different idempotency key",
                            code="activation_idempotency_conflict",
                        )
                    connection.commit()
                    return task, activation
                if task.status != "draft":
                    raise LearningActivationError(
                        "learning task is not a draft",
                        code="learning_task_not_draft",
                    )
                if not task.clarification.ready_to_activate:
                    raise LearningActivationError(
                        "learning task is not ready to activate",
                        code="learning_task_not_ready",
                    )
                reused_key = connection.execute(
                    "SELECT task_id, task_revision FROM learning_task_activations "
                    "WHERE owner_id = ? AND workspace_id = ? AND idempotency_key = ?",
                    (owner, workspace, key),
                ).fetchone()
                if reused_key is not None:
                    raise LearningActivationError(
                        "idempotency key already belongs to another learning task",
                        code="activation_idempotency_conflict",
                    )
                now = datetime.now(UTC).isoformat()
                digest = hashlib.sha256(
                    f"{owner}\0{workspace}\0{task_key}\0{expected_revision}\0{key}".encode()
                ).hexdigest()
                activation = LearningActivationRecord(
                    version=3,
                    owner_id=owner,
                    workspace_id=workspace,
                    task_id=task_key,
                    task_revision=expected_revision,
                    idempotency_key=key,
                    session_id=f"learning-{digest[:32]}",
                    kickoff_run_id=f"run_learning_{digest[:20]}",
                    thread_goal_revision=None,
                    learning_goal_ref=LearningGoalRef(
                        goal_id=f"learning-task-{digest[:20]}",
                        goal_revision=f"ltask-r{expected_revision}",
                    ),
                    learning_plan_id=task.learning_plan_id,
                    learning_plan_hash=task.learning_plan_hash,
                    turn_context_plan_id=f"turnplan_{digest[:24]}",
                    turn_context_plan_hash=None,
                    dag_hash=None,
                    catalog_revision=None,
                    capability_revision=None,
                    allowed_capabilities=(),
                    source_policy_snapshot=task.source_policy,
                    source_policy_revision=source_policy_revision(task.source_policy),
                    resume_validation_version="canonical_l0_v3",
                    receipt_status="activating",
                    stage="intent",
                    failure_code=None,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                )
                connection.execute(
                    "INSERT INTO learning_task_activations ("
                    "owner_id, workspace_id, task_id, task_revision, idempotency_key, session_id, "
                    "kickoff_run_id, receipt_json, status, stage, created_at, updated_at, completed_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        owner,
                        workspace,
                        task_key,
                        expected_revision,
                        key,
                        activation.session_id,
                        activation.kickoff_run_id,
                        _encode_activation(activation),
                        activation.receipt_status,
                        activation.stage,
                        now,
                        now,
                        None,
                    ),
                )
                activating_task = replace(task, status="activating", updated_at=now)
                _write_task(connection, owner, workspace, activating_task)
                connection.commit()
                return activating_task, activation
            except Exception:
                connection.rollback()
                raise

    def activation(
        self, *, owner_id: str, workspace_id: str, task_id: str
    ) -> LearningActivationRecord:
        owner = _bounded_owner(owner_id)
        workspace = _bounded_workspace(workspace_id)
        task_key = _bounded_task_id(task_id)
        self._ensure_ready()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT owner_id, workspace_id, task_id, task_revision, idempotency_key, session_id, "
                "kickoff_run_id, status, stage, receipt_json FROM learning_task_activations "
                "WHERE owner_id = ? AND workspace_id = ? AND task_id = ? "
                "ORDER BY task_revision DESC LIMIT 1",
                (owner, workspace, task_key),
            ).fetchone()
        if row is None:
            raise LearningActivationError(
                "learning activation not found",
                code="learning_activation_not_found",
            )
        return _decode_activation_row(row)

    def active_activation_for_session(
        self, *, owner_id: str, workspace_id: str, session_id: str
    ) -> LearningActivationRecord | None:
        """Resolve a server-owned active binding without trusting Session JSON markers."""
        owner = _bounded_owner(owner_id)
        workspace = _bounded_workspace(workspace_id)
        session_key = str(session_id).strip()
        if not session_key:
            raise ValueError("session_id must not be empty")
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT owner_id, workspace_id, task_id, task_revision, idempotency_key, session_id, "
                "kickoff_run_id, status, stage, receipt_json FROM learning_task_activations "
                "WHERE session_id = ? AND status = 'active' LIMIT 2",
                (session_key,),
            ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise LearningActivationError(
                "learning session has multiple active bindings",
                code="learning_activation_session_conflict",
            )
        activation = _decode_activation_row(rows[0])
        if activation.owner_id != owner or activation.workspace_id != workspace:
            raise LearningActivationError(
                "learning session scope mismatch",
                code="learning_activation_scope_mismatch",
            )
        return activation

    def active_activation_for_owner_session(
        self, *, owner_id: str, session_id: str
    ) -> LearningActivationRecord | None:
        """Resolve one canonical active binding without trusting Session storage."""
        owner = _bounded_owner(owner_id)
        session_key = str(session_id).strip()
        if not session_key:
            raise ValueError("session_id must not be empty")
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT owner_id, workspace_id, task_id, task_revision, idempotency_key, session_id, "
                "kickoff_run_id, status, stage, receipt_json FROM learning_task_activations "
                "WHERE owner_id = ? AND session_id = ? AND status = 'active' "
                "AND workspace_id IS NOT NULL LIMIT 2",
                (owner, session_key),
            ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise LearningActivationError(
                "learning session has multiple active owner bindings",
                code="learning_activation_session_conflict",
            )
        return _decode_activation_row(rows[0])

    def begin_kickoff(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> tuple[LearningTask, LearningActivationRecord, LearningKickoffDispatchRecord]:
        """Persist a revision-bound kickoff intent before touching the Session Journal."""
        if isinstance(expected_revision, bool) or expected_revision < 1:
            raise ValueError("expected_revision must be positive")
        owner = _bounded_owner(owner_id)
        workspace = _bounded_workspace(workspace_id)
        task_key = _bounded_task_id(task_id)
        key = _bounded_idempotency_key(idempotency_key)
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                task_row = connection.execute(
                    "SELECT payload_json FROM learning_tasks WHERE owner_id = ? "
                    "AND workspace_id = ? AND task_id = ?",
                    (owner, workspace, task_key),
                ).fetchone()
                if task_row is None:
                    raise LearningTaskNotFoundError(task_key)
                task = _decode_scoped_task(
                    str(task_row["payload_json"]), workspace_id=workspace, task_id=task_key
                )
                if task.task_revision != expected_revision:
                    raise LearningTaskConflictError(
                        "learning task revision conflict",
                        current_revision=task.task_revision,
                    )
                activation_row = connection.execute(
                    "SELECT owner_id, workspace_id, task_id, task_revision, idempotency_key, "
                    "session_id, kickoff_run_id, status, stage, receipt_json "
                    "FROM learning_task_activations WHERE owner_id = ? AND workspace_id = ? "
                    "AND task_id = ? AND task_revision = ?",
                    (owner, workspace, task_key, expected_revision),
                ).fetchone()
                if activation_row is None or task.status != "active":
                    raise LearningKickoffError(
                        "learning task must be active before kickoff",
                        code="learning_kickoff_activation_required",
                    )
                activation = _decode_activation_row(activation_row)
                if activation.receipt_status != "active" or activation.stage != "active":
                    raise LearningKickoffError(
                        "learning activation must be active before kickoff",
                        code="learning_kickoff_activation_required",
                    )
                existing = connection.execute(
                    "SELECT owner_id, workspace_id, task_id, task_revision, idempotency_key, "
                    "activation_idempotency_key, session_id, receipt_json, status, stage "
                    "FROM learning_task_kickoffs WHERE owner_id = ? AND workspace_id = ? "
                    "AND task_id = ? AND task_revision = ?",
                    (owner, workspace, task_key, expected_revision),
                ).fetchone()
                if existing is not None:
                    record = _decode_kickoff_row(existing)
                    if record.idempotency_key != key:
                        raise LearningKickoffError(
                            "learning task revision already uses a different kickoff key",
                            code="learning_kickoff_idempotency_conflict",
                        )
                    _validate_kickoff_binding(task=task, activation=activation, record=record)
                    connection.commit()
                    return task, activation, record
                reused_key = connection.execute(
                    "SELECT task_id, task_revision FROM learning_task_kickoffs "
                    "WHERE owner_id = ? AND workspace_id = ? AND idempotency_key = ?",
                    (owner, workspace, key),
                ).fetchone()
                if reused_key is not None:
                    raise LearningKickoffError(
                        "kickoff idempotency key already belongs to another learning task",
                        code="learning_kickoff_idempotency_conflict",
                    )
                now = datetime.now(UTC).isoformat()
                content_hash = _sha256(task.topic)
                digest = hashlib.sha256(
                    (
                        f"{owner}\0{workspace}\0{task_key}\0{expected_revision}\0"
                        f"{activation.idempotency_key}\0{key}\0{content_hash}"
                    ).encode()
                ).hexdigest()
                record = LearningKickoffDispatchRecord(
                    version=1,
                    owner_id=owner,
                    workspace_id=workspace,
                    task_id=task_key,
                    task_revision=expected_revision,
                    idempotency_key=key,
                    activation_idempotency_key_hash=_sha256(activation.idempotency_key),
                    kickoff_idempotency_key_hash=_sha256(key),
                    dispatch_id=f"lkick_{digest[:32]}",
                    session_id=activation.session_id,
                    message_id=f"learning-kickoff:{digest[:32]}",
                    acceptance_run_id=f"run_learning_accept_{digest[:20]}",
                    turn_run_id=f"run_learning_turn_{digest[:20]}",
                    content_hash=content_hash,
                    receipt_status="dispatching",
                    stage="intent",
                    created_at=now,
                    updated_at=now,
                    accepted_at=None,
                )
                connection.execute(
                    "INSERT INTO learning_task_kickoffs ("
                    "owner_id, workspace_id, task_id, task_revision, idempotency_key, "
                    "activation_idempotency_key, session_id, receipt_json, status, stage, "
                    "created_at, updated_at, accepted_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        owner,
                        workspace,
                        task_key,
                        expected_revision,
                        key,
                        activation.idempotency_key,
                        activation.session_id,
                        _encode_kickoff(record),
                        record.receipt_status,
                        record.stage,
                        now,
                        now,
                        None,
                    ),
                )
                connection.commit()
                return task, activation, record
            except Exception:
                connection.rollback()
                raise

    def save_kickoff(
        self,
        record: LearningKickoffDispatchRecord,
        *,
        stage: LearningKickoffStage,
        receipt_status: LearningKickoffReceiptStatus,
    ) -> LearningKickoffDispatchRecord:
        """Advance one kickoff receipt without allowing stale writers to regress it."""
        if (stage == "accepted") != (receipt_status == "accepted"):
            raise ValueError("accepted kickoff stage and status must advance together")
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT owner_id, workspace_id, task_id, task_revision, idempotency_key, "
                    "activation_idempotency_key, session_id, receipt_json, status, stage "
                    "FROM learning_task_kickoffs WHERE owner_id = ? AND workspace_id = ? "
                    "AND task_id = ? AND task_revision = ? AND idempotency_key = ?",
                    (
                        record.owner_id,
                        record.workspace_id,
                        record.task_id,
                        record.task_revision,
                        record.idempotency_key,
                    ),
                ).fetchone()
                if row is None:
                    raise LearningKickoffError(
                        "learning kickoff changed during dispatch",
                        code="learning_kickoff_conflict",
                    )
                current = _decode_kickoff_row(row)
                _validate_same_kickoff(current, record)
                if current.receipt_status == "accepted":
                    connection.commit()
                    return current
                if _kickoff_stage_rank(current.stage) > _kickoff_stage_rank(stage):
                    connection.commit()
                    return current
                now = datetime.now(UTC).isoformat()
                accepted_at = now if receipt_status == "accepted" else current.accepted_at
                updated = replace(
                    current,
                    receipt_status=receipt_status,
                    stage=stage,
                    updated_at=now,
                    accepted_at=accepted_at,
                )
                cursor = connection.execute(
                    "UPDATE learning_task_kickoffs SET receipt_json = ?, status = ?, stage = ?, "
                    "updated_at = ?, accepted_at = ? WHERE owner_id = ? AND workspace_id = ? "
                    "AND task_id = ? AND task_revision = ? AND idempotency_key = ?",
                    (
                        _encode_kickoff(updated),
                        updated.receipt_status,
                        updated.stage,
                        updated.updated_at,
                        updated.accepted_at,
                        updated.owner_id,
                        updated.workspace_id,
                        updated.task_id,
                        updated.task_revision,
                        updated.idempotency_key,
                    ),
                )
                if cursor.rowcount != 1:
                    raise LearningKickoffError(
                        "learning kickoff changed during dispatch",
                        code="learning_kickoff_conflict",
                    )
                connection.commit()
                return updated
            except Exception:
                connection.rollback()
                raise

    def kickoff(
        self, *, owner_id: str, workspace_id: str, task_id: str
    ) -> LearningKickoffDispatchRecord:
        self._ensure_ready()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT owner_id, workspace_id, task_id, task_revision, idempotency_key, "
                "activation_idempotency_key, session_id, receipt_json, status, stage "
                "FROM learning_task_kickoffs WHERE owner_id = ? AND workspace_id = ? "
                "AND task_id = ? ORDER BY task_revision DESC LIMIT 1",
                (
                    _bounded_owner(owner_id),
                    _bounded_workspace(workspace_id),
                    _bounded_task_id(task_id),
                ),
            ).fetchone()
        if row is None:
            raise LearningKickoffError(
                "learning kickoff receipt not found",
                code="learning_kickoff_not_found",
            )
        return _decode_kickoff_row(row)

    def accepted_kickoff_for_session(
        self, *, owner_id: str, workspace_id: str, session_id: str
    ) -> LearningKickoffDispatchRecord | None:
        session_key = str(session_id).strip()
        if not session_key:
            raise ValueError("session_id must not be empty")
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT owner_id, workspace_id, task_id, task_revision, idempotency_key, "
                "activation_idempotency_key, session_id, receipt_json, status, stage "
                "FROM learning_task_kickoffs WHERE owner_id = ? AND workspace_id = ? "
                "AND session_id = ? AND status = 'accepted' LIMIT 2",
                (_bounded_owner(owner_id), _bounded_workspace(workspace_id), session_key),
            ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise LearningKickoffError(
                "learning session has multiple accepted kickoffs",
                code="learning_kickoff_session_conflict",
            )
        return _decode_kickoff_row(rows[0])

    def save_activation(
        self,
        record: LearningActivationRecord,
        *,
        task_status: LearningActivationStatus,
        learning_goal_ref: LearningGoalRef | None = None,
        before_commit: Callable[[], None] | None = None,
    ) -> LearningActivationRecord:
        """Persist one bootstrap checkpoint and the matching task projection."""
        if before_commit is not None and task_status != "active":
            raise ValueError("before_commit is only valid for an active commit")
        self._ensure_ready()
        now = datetime.now(UTC).isoformat()
        completed_at = now if task_status == "active" else record.completed_at
        updated = replace(record, updated_at=now, completed_at=completed_at)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current_row = connection.execute(
                    "SELECT owner_id, workspace_id, task_id, task_revision, idempotency_key, session_id, "
                    "kickoff_run_id, status, stage, receipt_json FROM learning_task_activations "
                    "WHERE owner_id = ? AND workspace_id = ? "
                    "AND task_id = ? AND task_revision = ? AND idempotency_key = ?",
                    (
                        updated.owner_id,
                        updated.workspace_id,
                        updated.task_id,
                        updated.task_revision,
                        updated.idempotency_key,
                    ),
                ).fetchone()
                if current_row is None:
                    raise LearningActivationError(
                        "learning activation changed during bootstrap",
                        code="learning_activation_conflict",
                    )
                current = _decode_activation_row(current_row)
                if current.receipt_status == "active":
                    connection.commit()
                    return current
                if _activation_stage_rank(current.stage) > _activation_stage_rank(updated.stage):
                    connection.commit()
                    return current
                cursor = connection.execute(
                    "UPDATE learning_task_activations SET receipt_json = ?, status = ?, stage = ?, "
                    "updated_at = ?, completed_at = ? WHERE owner_id = ? AND workspace_id = ? "
                    "AND task_id = ? AND task_revision = ? AND idempotency_key = ?",
                    (
                        _encode_activation(updated),
                        updated.receipt_status,
                        updated.stage,
                        now,
                        completed_at,
                        updated.owner_id,
                        updated.workspace_id,
                        updated.task_id,
                        updated.task_revision,
                        updated.idempotency_key,
                    ),
                )
                if cursor.rowcount != 1:
                    raise LearningActivationError(
                        "learning activation changed during bootstrap",
                        code="learning_activation_conflict",
                    )
                row = connection.execute(
                    "SELECT payload_json FROM learning_tasks WHERE owner_id = ? "
                    "AND workspace_id = ? AND task_id = ?",
                    (updated.owner_id, updated.workspace_id, updated.task_id),
                ).fetchone()
                if row is None:
                    raise LearningTaskNotFoundError(updated.task_id)
                task = _decode_scoped_task(
                    str(row["payload_json"]),
                    workspace_id=updated.workspace_id,
                    task_id=updated.task_id,
                )
                if task.task_revision != updated.task_revision:
                    raise LearningTaskConflictError(
                        "learning task revision conflict",
                        current_revision=task.task_revision,
                    )
                if task_status == "active" and _has_future_l0_identity(task):
                    raise LearningActivationError(
                        "L0 task contains a future plan identity",
                        code="learning_activation_contract_conflict",
                    )
                if before_commit is not None:
                    before_commit()
                ref = learning_goal_ref or (
                    LearningGoalRef(**task.learning_goal_ref) if task.learning_goal_ref else None
                )
                projected = replace(
                    task,
                    status=task_status,
                    learning_goal_ref=(
                        {"goal_id": ref.goal_id, "goal_revision": ref.goal_revision}
                        if ref is not None
                        else None
                    ),
                    updated_at=now,
                )
                _write_task(connection, updated.owner_id, updated.workspace_id, projected)
                connection.commit()
                return updated
            except Exception:
                connection.rollback()
                raise

    def archive_session_if_failed(
        self,
        record: LearningActivationRecord,
        *,
        archive_session: Callable[[], None],
    ) -> bool:
        """Fence Session compensation against a newer or successful activation writer."""
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT owner_id, workspace_id, task_id, task_revision, idempotency_key, "
                    "session_id, kickoff_run_id, status, stage, receipt_json "
                    "FROM learning_task_activations WHERE owner_id = ? AND workspace_id = ? "
                    "AND task_id = ? AND task_revision = ? AND idempotency_key = ?",
                    (
                        record.owner_id,
                        record.workspace_id,
                        record.task_id,
                        record.task_revision,
                        record.idempotency_key,
                    ),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return False
                current = _decode_activation_row(row)
                if (
                    current.receipt_status != "activation_failed"
                    or current.stage != record.stage
                    or current.failure_code != record.failure_code
                    or current.updated_at != record.updated_at
                ):
                    connection.commit()
                    return False
                archive_session()
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def reconcilable_activations(self) -> tuple[LearningActivationRecord, ...]:
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT owner_id, workspace_id, task_id, task_revision, idempotency_key, session_id, "
                "kickoff_run_id, status, stage, receipt_json FROM learning_task_activations "
                "WHERE workspace_id IS NOT NULL "
                "AND status IN ('activating', 'activation_failed') ORDER BY created_at"
            ).fetchall()
        records: list[LearningActivationRecord] = []
        corrupt_count = 0
        for row in rows:
            try:
                records.append(_decode_activation_row(row))
            except LearningActivationError:
                corrupt_count += 1
        if corrupt_count:
            logger.error(
                "Skipped %d corrupt learning activation receipt(s) during reconciliation",
                corrupt_count,
            )
        return tuple(records)

    def legacy_active_activations(self) -> tuple[LearningLegacyActivationCandidate, ...]:
        """Return only legacy active rows that still need evidence-based workspace recovery."""
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT a.owner_id, a.task_id, a.task_revision, a.idempotency_key,
                          a.session_id, a.kickoff_run_id, a.receipt_json,
                          t.payload_json AS task_payload_json
                   FROM learning_task_activations AS a
                   JOIN learning_tasks AS t
                     ON t.owner_id = a.owner_id AND t.task_id = a.task_id
                   WHERE a.workspace_id IS NULL AND t.workspace_id IS NULL
                     AND a.status = 'active' AND t.status = 'active'
                   ORDER BY a.created_at"""
            ).fetchall()
        candidates: list[LearningLegacyActivationCandidate] = []
        for row in rows:
            try:
                task_data = json.loads(str(row["task_payload_json"]))
                receipt_data = json.loads(str(row["receipt_json"]))
                if not isinstance(task_data, dict) or not isinstance(receipt_data, dict):
                    raise TypeError("legacy learning payload must be an object")
                policy_data = task_data["source_policy"]
                if not isinstance(policy_data, dict):
                    raise TypeError("legacy source policy must be an object")
                policy = LearningSourcePolicy(
                    knowledge=policy_data["knowledge"],
                    web=policy_data["web"],
                    domains=tuple(policy_data.get("domains", ())),
                    freshness=policy_data["freshness"],
                )
                source_policy_revision(policy)
                if (
                    task_data.get("learning_plan_id") is not None
                    or task_data.get("learning_plan_hash") is not None
                    or task_data.get("dag_hash") is not None
                    or receipt_data.get("learning_plan_id") is not None
                    or receipt_data.get("learning_plan_hash") is not None
                    or receipt_data.get("dag_hash") is not None
                ):
                    raise ValueError("legacy L0 payload contains a future plan identity")
                catalog_revision = receipt_data.get("catalog_revision")
                capability_revision = receipt_data.get("capability_revision")
                allowed_capabilities = receipt_data.get("allowed_capabilities")
                if (
                    not isinstance(catalog_revision, str)
                    or not catalog_revision
                    or not isinstance(capability_revision, str)
                    or not capability_revision
                    or not isinstance(allowed_capabilities, list)
                    or any(not isinstance(item, str) or not item for item in allowed_capabilities)
                ):
                    raise ValueError("legacy capability binding is missing")
                plan_id = _read_renamed_field(
                    receipt_data,
                    canonical="turn_context_plan_id",
                    legacy="plan_id",
                    required=True,
                )
                plan_hash = _read_renamed_field(
                    receipt_data,
                    canonical="turn_context_plan_hash",
                    legacy="plan_hash",
                    required=True,
                )
                if plan_id is None or plan_hash is None:
                    raise ValueError("legacy turn context plan identity is missing")
                candidates.append(
                    LearningLegacyActivationCandidate(
                        owner_id=str(row["owner_id"]),
                        task_id=str(row["task_id"]),
                        task_revision=int(row["task_revision"]),
                        idempotency_key=str(row["idempotency_key"]),
                        session_id=str(row["session_id"]),
                        kickoff_run_id=str(row["kickoff_run_id"]),
                        turn_context_plan_id=plan_id,
                        turn_context_plan_hash=plan_hash,
                        source_policy=policy,
                        catalog_revision=catalog_revision,
                        capability_revision=capability_revision,
                        allowed_capabilities=tuple(allowed_capabilities),
                        task_payload_json=str(row["task_payload_json"]),
                        receipt_json=str(row["receipt_json"]),
                    )
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                logger.error("Skipped corrupt legacy learning workspace candidate")
        return tuple(candidates)

    def backfill_legacy_workspace(
        self, candidate: LearningLegacyActivationCandidate, *, workspace_id: str
    ) -> bool:
        """Atomically claim one validated legacy task and receipt for its proven workspace."""
        workspace = _bounded_workspace(workspace_id)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    """SELECT t.payload_json AS task_payload_json,
                              a.receipt_json AS receipt_json
                       FROM learning_tasks AS t
                       JOIN learning_task_activations AS a
                         ON a.owner_id = t.owner_id AND a.task_id = t.task_id
                       WHERE t.owner_id = ? AND t.task_id = ? AND t.task_revision = ?
                         AND t.workspace_id IS NULL AND a.workspace_id IS NULL
                         AND t.status = 'active' AND a.status = 'active'""",
                    (candidate.owner_id, candidate.task_id, candidate.task_revision),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return False
                if (
                    str(row["task_payload_json"]) != candidate.task_payload_json
                    or str(row["receipt_json"]) != candidate.receipt_json
                ):
                    raise LearningActivationError(
                        "legacy learning activation changed during migration",
                        code="learning_activation_conflict",
                    )
                task_data = json.loads(candidate.task_payload_json)
                receipt_data = json.loads(candidate.receipt_json)
                task_data["workspace_id"] = workspace
                task_data.setdefault("dag_hash", None)
                receipt_data.update(
                    {
                        "version": 3,
                        "workspace_id": workspace,
                        "source_policy_snapshot": {
                            "knowledge": candidate.source_policy.knowledge,
                            "web": candidate.source_policy.web,
                            "domains": list(candidate.source_policy.domains),
                            "freshness": candidate.source_policy.freshness,
                        },
                        "source_policy_revision": source_policy_revision(candidate.source_policy),
                        "resume_validation_version": "legacy_l0_v2",
                    }
                )
                receipt_data.setdefault("turn_context_plan_id", candidate.turn_context_plan_id)
                receipt_data.setdefault("turn_context_plan_hash", candidate.turn_context_plan_hash)
                receipt_data.setdefault("learning_plan_id", None)
                receipt_data.setdefault("learning_plan_hash", None)
                receipt_data.setdefault("dag_hash", None)
                receipt_data.pop("plan_id", None)
                receipt_data.pop("plan_hash", None)
                task_payload = json.dumps(
                    task_data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                )
                receipt_payload = json.dumps(
                    receipt_data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                )
                task = _decode(task_payload)
                activation = _decode_activation(receipt_payload)
                if (
                    task.workspace_id != workspace
                    or activation.workspace_id != workspace
                    or activation.owner_id != candidate.owner_id
                    or activation.task_id != candidate.task_id
                ):
                    raise ValueError("legacy workspace migration binding mismatch")
                connection.execute(
                    "UPDATE learning_tasks SET workspace_id = ?, payload_json = ? "
                    "WHERE owner_id = ? AND task_id = ? AND workspace_id IS NULL",
                    (workspace, task_payload, candidate.owner_id, candidate.task_id),
                )
                connection.execute(
                    "UPDATE learning_task_activations SET workspace_id = ?, receipt_json = ? "
                    "WHERE owner_id = ? AND task_id = ? AND task_revision = ? "
                    "AND workspace_id IS NULL",
                    (
                        workspace,
                        receipt_payload,
                        candidate.owner_id,
                        candidate.task_id,
                        candidate.task_revision,
                    ),
                )
                connection.commit()
                return True
            except Exception:
                connection.rollback()
                raise

    def block_unclaimed_legacy(self) -> int:
        """Block every legacy task that cannot be assigned without guessing a workspace."""
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT owner_id, task_id, payload_json FROM learning_tasks "
                "WHERE workspace_id IS NULL AND status != 'blocked'"
            ).fetchall()
            blocked = 0
            for row in rows:
                try:
                    data = json.loads(str(row["payload_json"]))
                    if not isinstance(data, dict):
                        raise TypeError
                    data["status"] = "blocked"
                    payload = json.dumps(
                        data, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                    )
                except (TypeError, json.JSONDecodeError):
                    payload = str(row["payload_json"])
                connection.execute(
                    "UPDATE learning_tasks SET status = 'blocked', payload_json = ? "
                    "WHERE owner_id = ? AND task_id = ? AND workspace_id IS NULL",
                    (payload, row["owner_id"], row["task_id"]),
                )
                blocked += 1
            connection.commit()
        return blocked

    def _ensure_ready(self) -> None:
        with self._lock:
            if self._initialized:
                return
            self._validate_path()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._validate_path()
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    _migrate_schema(connection)
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            self._initialized = True

    def _validate_path(self) -> None:
        if self.path.is_symlink():
            raise ValueError("learning task database must not be a symlink")
        if self.path.parent.exists() and self.path.parent.is_symlink():
            raise ValueError("learning task database directory must not be a symlink")

    def _connect(self) -> sqlite3.Connection:
        self._validate_path()
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection


class LearningTaskService:
    """Application service for draft-only learning-task behavior."""

    def __init__(self, repository: LearningTaskRepository) -> None:
        self.repository = repository

    def create_draft(
        self, *, owner_id: str, workspace_id: str, request: LearningTaskCreate
    ) -> LearningTask:
        return self.repository.create(owner_id=owner_id, workspace_id=workspace_id, request=request)

    def get(self, *, owner_id: str, workspace_id: str, task_id: str) -> LearningTask:
        return self.repository.get(owner_id=owner_id, workspace_id=workspace_id, task_id=task_id)

    def list(self, *, owner_id: str, workspace_id: str) -> tuple[LearningTask, ...]:
        return self.repository.list(owner_id=owner_id, workspace_id=workspace_id)

    def update_draft(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        expected_revision: int,
        patch: LearningTaskPatch,
    ) -> LearningTask:
        return self.repository.update_draft(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=task_id,
            expected_revision=expected_revision,
            patch=patch,
        )


def _encode(task: LearningTask) -> str:
    return json.dumps(asdict(task), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _decode(payload: str) -> LearningTask:
    data = json.loads(payload)
    profile = data["learner_profile"]
    policy = data["source_policy"]
    clarification = data["clarification"]
    return LearningTask(
        version=int(data["version"]),
        workspace_id=_bounded_workspace(str(data["workspace_id"])),
        task_id=str(data["task_id"]),
        task_revision=int(data["task_revision"]),
        template_id=str(data["template_id"]),
        topic=str(data["topic"]),
        desired_outcome=(
            str(data["desired_outcome"]) if data.get("desired_outcome") is not None else None
        ),
        learner_profile=LearningLearnerProfile(
            starting_level=profile.get("starting_level"),
            time_budget_minutes_per_week=profile.get("time_budget_minutes_per_week"),
            target_date=profile.get("target_date"),
        ),
        source_policy=LearningSourcePolicy(
            knowledge=policy["knowledge"],
            web=policy["web"],
            domains=tuple(policy.get("domains", ())),
            freshness=policy["freshness"],
        ),
        risk_class=data["risk_class"],
        risk_notice=data.get("risk_notice"),
        clarification=LearningClarification(
            required_fields=tuple(clarification.get("required_fields", ())),
            questions=tuple(
                LearningClarificationQuestion(field=item["field"], prompt=item["prompt"])
                for item in clarification.get("questions", ())
            ),
            ready_to_activate=bool(clarification["ready_to_activate"]),
        ),
        learning_plan_id=(
            str(data["learning_plan_id"]) if data.get("learning_plan_id") is not None else None
        ),
        learning_plan_hash=(
            str(data["learning_plan_hash"]) if data.get("learning_plan_hash") is not None else None
        ),
        dag_hash=str(data["dag_hash"]) if data.get("dag_hash") is not None else None,
        learning_goal_ref=(
            dict(data["learning_goal_ref"]) if data.get("learning_goal_ref") else None
        ),
        status=data["status"],
        created_at=str(data["created_at"]),
        updated_at=str(data["updated_at"]),
    )


def _decode_scoped_task(payload: str, *, workspace_id: str, task_id: str) -> LearningTask:
    task = _decode(payload)
    if task.workspace_id != workspace_id or task.task_id != task_id:
        raise LearningTaskNotFoundError(task_id)
    return task


def _bounded_owner(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError("owner_id must contain 1 to 255 characters")
    return normalized


def _bounded_workspace(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("workspace_id must contain 1 to 128 characters")
    return normalized


def _bounded_task_id(value: str) -> str:
    normalized = value.strip()
    if not normalized.startswith("ltask_") or len(normalized) > 128:
        raise ValueError("invalid learning task id")
    return normalized


def _bounded_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not 1 <= len(normalized) <= 200:
        raise ValueError("idempotency key must contain 1 to 200 characters")
    if any(ord(character) < 33 or ord(character) > 126 for character in normalized):
        raise ValueError("idempotency key must contain visible ASCII characters")
    return normalized


def _write_task(
    connection: sqlite3.Connection,
    owner_id: str,
    workspace_id: str,
    task: LearningTask,
) -> None:
    cursor = connection.execute(
        "UPDATE learning_tasks SET status = ?, payload_json = ?, updated_at = ? "
        "WHERE owner_id = ? AND workspace_id = ? AND task_id = ? AND task_revision = ?",
        (
            task.status,
            _encode(task),
            task.updated_at,
            owner_id,
            workspace_id,
            task.task_id,
            task.task_revision,
        ),
    )
    if cursor.rowcount != 1:
        raise LearningTaskConflictError(
            "learning task changed during activation",
            current_revision=task.task_revision,
        )


def _encode_activation(record: LearningActivationRecord) -> str:
    return json.dumps(asdict(record), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _encode_kickoff(record: LearningKickoffDispatchRecord) -> str:
    return json.dumps(asdict(record), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _decode_kickoff(payload: str) -> LearningKickoffDispatchRecord:
    data = json.loads(payload)
    if not isinstance(data, dict) or int(data.get("version", 0)) != 1:
        raise ValueError("unsupported learning kickoff receipt")
    status = data.get("receipt_status")
    stage = data.get("stage")
    if status not in {"dispatching", "accepted"}:
        raise ValueError("invalid learning kickoff status")
    if stage not in {"intent", "journal", "accepted"}:
        raise ValueError("invalid learning kickoff stage")
    if (status == "accepted") != (stage == "accepted"):
        raise ValueError("learning kickoff accepted state mismatch")
    accepted_at = data.get("accepted_at")
    if status == "accepted" and not isinstance(accepted_at, str):
        raise ValueError("accepted learning kickoff is missing accepted_at")
    return LearningKickoffDispatchRecord(
        version=1,
        owner_id=_bounded_owner(str(data["owner_id"])),
        workspace_id=_bounded_workspace(str(data["workspace_id"])),
        task_id=_bounded_task_id(str(data["task_id"])),
        task_revision=int(data["task_revision"]),
        idempotency_key=_bounded_idempotency_key(str(data["idempotency_key"])),
        activation_idempotency_key_hash=str(data["activation_idempotency_key_hash"]),
        kickoff_idempotency_key_hash=str(data["kickoff_idempotency_key_hash"]),
        dispatch_id=str(data["dispatch_id"]),
        session_id=str(data["session_id"]),
        message_id=str(data["message_id"]),
        acceptance_run_id=str(data["acceptance_run_id"]),
        turn_run_id=str(data["turn_run_id"]),
        content_hash=str(data["content_hash"]),
        receipt_status=cast(LearningKickoffReceiptStatus, status),
        stage=cast(LearningKickoffStage, stage),
        created_at=str(data["created_at"]),
        updated_at=str(data["updated_at"]),
        accepted_at=str(accepted_at) if accepted_at is not None else None,
    )


def _decode_kickoff_row(row: sqlite3.Row) -> LearningKickoffDispatchRecord:
    try:
        record = _decode_kickoff(str(row["receipt_json"]))
        expected = {
            "owner_id": record.owner_id,
            "workspace_id": record.workspace_id,
            "task_id": record.task_id,
            "task_revision": record.task_revision,
            "idempotency_key": record.idempotency_key,
            "session_id": record.session_id,
            "status": record.receipt_status,
            "stage": record.stage,
        }
        if any(row[key] != value for key, value in expected.items()):
            raise ValueError("kickoff row binding mismatch")
        if _sha256(str(row["activation_idempotency_key"])) != (
            record.activation_idempotency_key_hash
        ):
            raise ValueError("kickoff activation binding mismatch")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LearningKickoffError(
            "learning kickoff receipt is corrupt",
            code="learning_kickoff_corrupt",
        ) from exc
    return record


def _validate_kickoff_binding(
    *,
    task: LearningTask,
    activation: LearningActivationRecord,
    record: LearningKickoffDispatchRecord,
) -> None:
    if (
        record.task_id != task.task_id
        or record.task_revision != task.task_revision
        or record.session_id != activation.session_id
        or record.activation_idempotency_key_hash != _sha256(activation.idempotency_key)
        or record.content_hash != _sha256(task.topic)
    ):
        raise LearningKickoffError(
            "learning kickoff canonical binding changed",
            code="learning_kickoff_binding_conflict",
        )


def _validate_same_kickoff(
    current: LearningKickoffDispatchRecord,
    candidate: LearningKickoffDispatchRecord,
) -> None:
    immutable = (
        "owner_id",
        "workspace_id",
        "task_id",
        "task_revision",
        "idempotency_key",
        "activation_idempotency_key_hash",
        "kickoff_idempotency_key_hash",
        "dispatch_id",
        "session_id",
        "message_id",
        "acceptance_run_id",
        "turn_run_id",
        "content_hash",
        "created_at",
    )
    if any(getattr(current, field) != getattr(candidate, field) for field in immutable):
        raise LearningKickoffError(
            "learning kickoff immutable binding changed",
            code="learning_kickoff_conflict",
        )


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _decode_activation(payload: str) -> LearningActivationRecord:
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise TypeError("learning activation receipt must be an object")
    stored_version = int(data["version"])
    if stored_version not in {1, 2, 3}:
        raise ValueError("unsupported learning activation receipt version")
    ref = data["learning_goal_ref"]
    if not isinstance(ref, dict):
        raise TypeError("learning activation goal reference must be an object")
    status = data["receipt_status"]
    if status not in {"activating", "activation_failed", "active"}:
        raise ValueError("invalid learning activation status")
    stored_stage = str(data["stage"])
    stage = "turn_context_plan" if stored_stage == "plan" else stored_stage
    if stage not in {"intent", "session", "goal", "turn_context_plan", "active"}:
        raise ValueError("invalid learning activation stage")
    normalized_stage = cast(LearningActivationStage, stage)
    raw_capabilities = data.get("allowed_capabilities", ())
    if not isinstance(raw_capabilities, list) or any(
        not isinstance(item, str) or not item for item in raw_capabilities
    ):
        raise TypeError("invalid learning activation capabilities")
    raw_source_policy = data.get("source_policy_snapshot")
    if not isinstance(raw_source_policy, dict):
        raise TypeError("missing learning activation source policy snapshot")
    source_policy = LearningSourcePolicy(
        knowledge=raw_source_policy.get("knowledge", ""),
        web=raw_source_policy.get("web", ""),
        domains=tuple(raw_source_policy.get("domains", ())),
        freshness=raw_source_policy.get("freshness", ""),
    )
    expected_source_revision = source_policy_revision(source_policy)
    stored_source_revision = str(data.get("source_policy_revision", ""))
    if stored_source_revision != expected_source_revision:
        raise ValueError("learning activation source policy revision mismatch")
    validation_version = data.get("resume_validation_version", "canonical_l0_v3")
    if validation_version not in {"canonical_l0_v3", "legacy_l0_v2"}:
        raise ValueError("unsupported learning resume validation version")
    if stage == "active" and status != "active":
        raise ValueError("active learning activation stage requires an active status")
    if status == "active" and stage != "active":
        raise ValueError("active learning activation status requires an active stage")
    if (
        data.get("learning_plan_id") is not None
        or data.get("learning_plan_hash") is not None
        or data.get("dag_hash") is not None
    ):
        raise ValueError("L0 activation receipt contains a future plan identity")
    turn_context_plan_id = _read_renamed_field(
        data,
        canonical="turn_context_plan_id",
        legacy="plan_id",
        required=True,
    )
    if turn_context_plan_id is None:
        raise ValueError("missing turn_context_plan_id")
    turn_context_plan_hash = _read_renamed_field(
        data,
        canonical="turn_context_plan_hash",
        legacy="plan_hash",
        required=False,
    )
    return LearningActivationRecord(
        version=3,
        owner_id=str(data["owner_id"]),
        workspace_id=_bounded_workspace(str(data["workspace_id"])),
        task_id=str(data["task_id"]),
        task_revision=int(data["task_revision"]),
        idempotency_key=str(data["idempotency_key"]),
        session_id=str(data["session_id"]),
        kickoff_run_id=str(data["kickoff_run_id"]),
        thread_goal_revision=(
            int(data["thread_goal_revision"])
            if data.get("thread_goal_revision") is not None
            else None
        ),
        learning_goal_ref=LearningGoalRef(
            goal_id=str(ref["goal_id"]), goal_revision=str(ref["goal_revision"])
        ),
        learning_plan_id=(
            str(data["learning_plan_id"]) if data.get("learning_plan_id") is not None else None
        ),
        learning_plan_hash=(
            str(data["learning_plan_hash"]) if data.get("learning_plan_hash") is not None else None
        ),
        turn_context_plan_id=turn_context_plan_id,
        turn_context_plan_hash=turn_context_plan_hash,
        dag_hash=str(data["dag_hash"]) if data.get("dag_hash") is not None else None,
        catalog_revision=(
            str(data["catalog_revision"]) if data.get("catalog_revision") is not None else None
        ),
        capability_revision=(
            str(data["capability_revision"])
            if data.get("capability_revision") is not None
            else None
        ),
        allowed_capabilities=tuple(raw_capabilities),
        source_policy_snapshot=source_policy,
        source_policy_revision=stored_source_revision,
        resume_validation_version=validation_version,
        receipt_status=status,
        stage=normalized_stage,
        failure_code=str(data["failure_code"]) if data.get("failure_code") else None,
        created_at=str(data["created_at"]),
        updated_at=str(data["updated_at"]),
        completed_at=str(data["completed_at"]) if data.get("completed_at") else None,
    )


def _read_renamed_field(
    data: dict[str, object],
    *,
    canonical: str,
    legacy: str,
    required: bool,
) -> str | None:
    """Read one v2 field while accepting an equivalent v1 stored name."""

    canonical_value = data.get(canonical)
    legacy_value = data.get(legacy)
    if canonical_value is not None and legacy_value is not None and canonical_value != legacy_value:
        raise ValueError(f"conflicting {canonical} and legacy {legacy}")
    value = canonical_value if canonical_value is not None else legacy_value
    if value is None:
        if required:
            raise ValueError(f"missing {canonical}")
        return None
    if not isinstance(value, str) or not value:
        raise TypeError(f"invalid {canonical}")
    return value


def _decode_activation_row(row: sqlite3.Row) -> LearningActivationRecord:
    try:
        activation = _decode_activation(str(row["receipt_json"]))
        expected = {
            "owner_id": activation.owner_id,
            "workspace_id": activation.workspace_id,
            "task_id": activation.task_id,
            "task_revision": activation.task_revision,
            "idempotency_key": activation.idempotency_key,
            "session_id": activation.session_id,
            "kickoff_run_id": activation.kickoff_run_id,
        }
        stage_matches = row["stage"] == activation.stage or (
            row["stage"] == "plan" and activation.stage == "turn_context_plan"
        )
        status_matches = row["status"] == activation.receipt_status or (
            row["status"] == "superseded" and activation.receipt_status == "activation_failed"
        )
        if (
            not status_matches
            or not stage_matches
            or any(row[key] != value for key, value in expected.items())
        ):
            raise ValueError("activation row binding mismatch")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LearningActivationError(
            "learning activation receipt is corrupt",
            code="learning_activation_corrupt",
        ) from exc
    return activation


def _migrate_schema(connection: sqlite3.Connection) -> None:
    """Expand workspace scope while leaving unverifiable legacy rows unclaimed."""
    connection.execute(_TASK_TABLE_SQL)
    task_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(learning_tasks)").fetchall()
    }
    if "workspace_id" not in task_columns:
        connection.execute("ALTER TABLE learning_tasks ADD COLUMN workspace_id TEXT")

    activation_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' " "AND name = 'learning_task_activations'"
    ).fetchone()
    if activation_exists is None:
        connection.execute(_ACTIVATION_TABLE_SQL)
    else:
        activation_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(learning_task_activations)").fetchall()
        }
        if "workspace_id" not in activation_columns:
            connection.execute(
                "ALTER TABLE learning_task_activations RENAME TO learning_task_activations_legacy"
            )
            connection.execute(_ACTIVATION_TABLE_SQL)
            connection.execute(
                """INSERT INTO learning_task_activations (
                    owner_id, workspace_id, task_id, task_revision, idempotency_key,
                    session_id, kickoff_run_id, receipt_json, status, stage,
                    created_at, updated_at, completed_at
                ) SELECT owner_id, NULL, task_id, task_revision, idempotency_key,
                    session_id, kickoff_run_id, receipt_json, status, stage,
                    created_at, updated_at, completed_at
                FROM learning_task_activations_legacy"""
            )
            connection.execute("DROP TABLE learning_task_activations_legacy")

    connection.execute("DROP INDEX IF EXISTS learning_tasks_owner_updated_idx")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS learning_tasks_owner_workspace_updated_idx "
        "ON learning_tasks(owner_id, workspace_id, updated_at DESC)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS learning_task_activations_status_idx "
        "ON learning_task_activations(status, updated_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS learning_task_activations_active_session_idx "
        "ON learning_task_activations(session_id, status)"
    )
    connection.execute(_KICKOFF_TABLE_SQL)
    connection.execute(
        "CREATE INDEX IF NOT EXISTS learning_task_kickoffs_status_idx "
        "ON learning_task_kickoffs(status, updated_at)"
    )


def _activation_stage_rank(stage: str) -> int:
    return {
        "intent": 0,
        "session": 1,
        "goal": 2,
        "turn_context_plan": 3,
        "active": 4,
    }[stage]


def _kickoff_stage_rank(stage: str) -> int:
    return {"intent": 0, "journal": 1, "accepted": 2}[stage]


def _has_future_l0_identity(task: LearningTask) -> bool:
    return (
        task.learning_plan_id is not None
        or task.learning_plan_hash is not None
        or task.dag_hash is not None
    )


__all__ = [
    "LearningTaskConflictError",
    "LearningTaskError",
    "LearningTaskNotFoundError",
    "LearningTaskRepository",
    "LearningTaskService",
]
