"""SQLite persistence and CAS updates for Sage learning-task contracts."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
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
)

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_tasks (
    owner_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL CHECK (task_revision >= 1),
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, task_id)
);
CREATE INDEX IF NOT EXISTS learning_tasks_owner_updated_idx
ON learning_tasks(owner_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS learning_task_activations (
    owner_id TEXT NOT NULL,
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
    PRIMARY KEY (owner_id, task_id, task_revision),
    UNIQUE (owner_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS learning_task_activations_status_idx
ON learning_task_activations(status, updated_at);
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

    def create(self, *, owner_id: str, request: LearningTaskCreate) -> LearningTask:
        normalized = normalize_task_create(request)
        now = datetime.now(UTC).isoformat()
        policy = resolve_source_policy(normalized.topic, normalized.source_policy)
        risk_class, risk_notice = risk_for_topic(normalized.topic)
        task = LearningTask(
            version=1,
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
                    owner_id, task_id, task_revision, status, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    owner,
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

    def get(self, *, owner_id: str, task_id: str) -> LearningTask:
        self._ensure_ready()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM learning_tasks WHERE owner_id = ? AND task_id = ?",
                (_bounded_owner(owner_id), _bounded_task_id(task_id)),
            ).fetchone()
        if row is None:
            raise LearningTaskNotFoundError(task_id)
        return _decode(str(row["payload_json"]))

    def update_draft(
        self,
        *,
        owner_id: str,
        task_id: str,
        expected_revision: int,
        patch: LearningTaskPatch,
    ) -> LearningTask:
        if isinstance(expected_revision, bool) or expected_revision < 1:
            raise ValueError("expected_revision must be positive")
        owner = _bounded_owner(owner_id)
        task_key = _bounded_task_id(task_id)
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT payload_json FROM learning_tasks WHERE owner_id = ? AND task_id = ?",
                (owner, task_key),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise LearningTaskNotFoundError(task_key)
            current = _decode(str(row["payload_json"]))
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
                   WHERE owner_id = ? AND task_id = ? AND task_revision = ?""",
                (
                    updated.task_revision,
                    updated.status,
                    _encode(updated),
                    updated.updated_at,
                    owner,
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
                    "WHERE owner_id = ? AND task_id = ? AND task_revision = ? "
                    "AND status = 'activation_failed'",
                    (updated.updated_at, owner, task_key, expected_revision),
                )
            connection.commit()
        return updated

    def begin_activation(
        self,
        *,
        owner_id: str,
        task_id: str,
        expected_revision: int,
        idempotency_key: str,
    ) -> tuple[LearningTask, LearningActivationRecord]:
        """Persist one stable activation intent before creating external resources."""
        if isinstance(expected_revision, bool) or expected_revision < 1:
            raise ValueError("expected_revision must be positive")
        owner = _bounded_owner(owner_id)
        task_key = _bounded_task_id(task_id)
        key = _bounded_idempotency_key(idempotency_key)
        self._ensure_ready()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT payload_json FROM learning_tasks WHERE owner_id = ? AND task_id = ?",
                    (owner, task_key),
                ).fetchone()
                if row is None:
                    raise LearningTaskNotFoundError(task_key)
                task = _decode(str(row["payload_json"]))
                if task.task_revision != expected_revision:
                    raise LearningTaskConflictError(
                        "learning task revision conflict",
                        current_revision=task.task_revision,
                    )
                existing = connection.execute(
                    "SELECT owner_id, task_id, task_revision, idempotency_key, session_id, "
                    "kickoff_run_id, status, stage, receipt_json FROM learning_task_activations "
                    "WHERE owner_id = ? AND task_id = ? AND task_revision = ?",
                    (owner, task_key, expected_revision),
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
                    "WHERE owner_id = ? AND idempotency_key = ?",
                    (owner, key),
                ).fetchone()
                if reused_key is not None:
                    raise LearningActivationError(
                        "idempotency key already belongs to another learning task",
                        code="activation_idempotency_conflict",
                    )
                now = datetime.now(UTC).isoformat()
                digest = hashlib.sha256(
                    f"{owner}\0{task_key}\0{expected_revision}\0{key}".encode()
                ).hexdigest()
                activation = LearningActivationRecord(
                    version=2,
                    owner_id=owner,
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
                    receipt_status="activating",
                    stage="intent",
                    failure_code=None,
                    created_at=now,
                    updated_at=now,
                    completed_at=None,
                )
                connection.execute(
                    "INSERT INTO learning_task_activations ("
                    "owner_id, task_id, task_revision, idempotency_key, session_id, "
                    "kickoff_run_id, receipt_json, status, stage, created_at, updated_at, completed_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        owner,
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
                _write_task(connection, owner, activating_task)
                connection.commit()
                return activating_task, activation
            except Exception:
                connection.rollback()
                raise

    def activation(self, *, owner_id: str, task_id: str) -> LearningActivationRecord:
        owner = _bounded_owner(owner_id)
        task_key = _bounded_task_id(task_id)
        self._ensure_ready()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT owner_id, task_id, task_revision, idempotency_key, session_id, "
                "kickoff_run_id, status, stage, receipt_json FROM learning_task_activations "
                "WHERE owner_id = ? AND task_id = ? ORDER BY task_revision DESC LIMIT 1",
                (owner, task_key),
            ).fetchone()
        if row is None:
            raise LearningActivationError(
                "learning activation not found",
                code="learning_activation_not_found",
            )
        return _decode_activation_row(row)

    def save_activation(
        self,
        record: LearningActivationRecord,
        *,
        task_status: LearningActivationStatus,
        learning_goal_ref: LearningGoalRef | None = None,
    ) -> LearningActivationRecord:
        """Persist one bootstrap checkpoint and the matching task projection."""
        self._ensure_ready()
        now = datetime.now(UTC).isoformat()
        completed_at = now if task_status == "active" else record.completed_at
        updated = replace(record, updated_at=now, completed_at=completed_at)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current_row = connection.execute(
                    "SELECT owner_id, task_id, task_revision, idempotency_key, session_id, "
                    "kickoff_run_id, status, stage, receipt_json FROM learning_task_activations "
                    "WHERE owner_id = ? "
                    "AND task_id = ? AND task_revision = ? AND idempotency_key = ?",
                    (
                        updated.owner_id,
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
                    "updated_at = ?, completed_at = ? WHERE owner_id = ? AND task_id = ? "
                    "AND task_revision = ? AND idempotency_key = ?",
                    (
                        _encode_activation(updated),
                        updated.receipt_status,
                        updated.stage,
                        now,
                        completed_at,
                        updated.owner_id,
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
                    "SELECT payload_json FROM learning_tasks WHERE owner_id = ? AND task_id = ?",
                    (updated.owner_id, updated.task_id),
                ).fetchone()
                if row is None:
                    raise LearningTaskNotFoundError(updated.task_id)
                task = _decode(str(row["payload_json"]))
                if task.task_revision != updated.task_revision:
                    raise LearningTaskConflictError(
                        "learning task revision conflict",
                        current_revision=task.task_revision,
                    )
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
                _write_task(connection, updated.owner_id, projected)
                connection.commit()
                return updated
            except Exception:
                connection.rollback()
                raise

    def reconcilable_activations(self) -> tuple[LearningActivationRecord, ...]:
        self._ensure_ready()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT owner_id, task_id, task_revision, idempotency_key, session_id, "
                "kickoff_run_id, status, stage, receipt_json FROM learning_task_activations "
                "WHERE status IN ('activating', 'activation_failed') ORDER BY created_at"
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

    def _ensure_ready(self) -> None:
        with self._lock:
            if self._initialized:
                return
            self._validate_path()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._validate_path()
            with self._connect() as connection:
                connection.executescript(_SCHEMA)
                connection.commit()
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

    def create_draft(self, *, owner_id: str, request: LearningTaskCreate) -> LearningTask:
        return self.repository.create(owner_id=owner_id, request=request)

    def get(self, *, owner_id: str, task_id: str) -> LearningTask:
        return self.repository.get(owner_id=owner_id, task_id=task_id)

    def update_draft(
        self,
        *,
        owner_id: str,
        task_id: str,
        expected_revision: int,
        patch: LearningTaskPatch,
    ) -> LearningTask:
        return self.repository.update_draft(
            owner_id=owner_id,
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
        learning_goal_ref=(
            dict(data["learning_goal_ref"]) if data.get("learning_goal_ref") else None
        ),
        status=data["status"],
        created_at=str(data["created_at"]),
        updated_at=str(data["updated_at"]),
    )


def _bounded_owner(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError("owner_id must contain 1 to 255 characters")
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


def _write_task(connection: sqlite3.Connection, owner_id: str, task: LearningTask) -> None:
    cursor = connection.execute(
        "UPDATE learning_tasks SET status = ?, payload_json = ?, updated_at = ? "
        "WHERE owner_id = ? AND task_id = ? AND task_revision = ?",
        (task.status, _encode(task), task.updated_at, owner_id, task.task_id, task.task_revision),
    )
    if cursor.rowcount != 1:
        raise LearningTaskConflictError(
            "learning task changed during activation",
            current_revision=task.task_revision,
        )


def _encode_activation(record: LearningActivationRecord) -> str:
    return json.dumps(asdict(record), ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _decode_activation(payload: str) -> LearningActivationRecord:
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise TypeError("learning activation receipt must be an object")
    stored_version = int(data["version"])
    if stored_version not in {1, 2}:
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
    if stage == "active" and status != "active":
        raise ValueError("active learning activation stage requires an active status")
    if status == "active" and stage != "active":
        raise ValueError("active learning activation status requires an active stage")
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
        version=2,
        owner_id=str(data["owner_id"]),
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


def _activation_stage_rank(stage: str) -> int:
    return {
        "intent": 0,
        "session": 1,
        "goal": 2,
        "turn_context_plan": 3,
        "active": 4,
    }[stage]


__all__ = [
    "LearningTaskConflictError",
    "LearningTaskError",
    "LearningTaskNotFoundError",
    "LearningTaskRepository",
    "LearningTaskService",
]
