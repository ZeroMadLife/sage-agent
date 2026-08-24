"""Durable Learning Artifact, plan, checkpoint, and browser-safe resume storage."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Literal, cast, get_args
from urllib.parse import urlsplit

from core.learning.materials import (
    KnowledgeUnit,
    LearningMapArtifact,
    LearningPlan,
    LearningUnitStatus,
)
from core.learning.tasks import LearningSourcePolicy, LearningTask, source_policy_revision

LearningCheckpointStage = Literal[
    "knowledge_pending",
    "knowledge_ready",
    "source_gap",
    "research_pending",
    "research_ready",
    "user_input_pending",
    "approval_pending",
    "synthesize_pending",
    "artifact_ready",
    "blocked",
]

_PLAN_TABLE = """
CREATE TABLE IF NOT EXISTS learning_plans (
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    source_policy_revision TEXT NOT NULL,
    capability_revision TEXT NOT NULL,
    catalog_revision TEXT NOT NULL,
    dag_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, workspace_id, task_id),
    UNIQUE (owner_id, workspace_id, plan_id)
)
"""

_ARTIFACT_TABLE = """
CREATE TABLE IF NOT EXISTS learning_artifacts (
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    artifact_ref TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    media_type TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    source_revisions_json TEXT NOT NULL,
    citations_json TEXT NOT NULL,
    idempotency_key_hash TEXT NOT NULL,
    retention TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, workspace_id, artifact_id),
    UNIQUE (owner_id, workspace_id, task_id, task_revision, idempotency_key_hash),
    UNIQUE (artifact_ref)
)
"""

_CHECKPOINT_TABLE = """
CREATE TABLE IF NOT EXISTS learning_checkpoints (
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    dag_hash TEXT NOT NULL,
    source_policy_revision TEXT NOT NULL,
    capability_revision TEXT NOT NULL,
    checkpoint_revision INTEGER NOT NULL,
    stage TEXT NOT NULL,
    next_action TEXT NOT NULL,
    evidence_count INTEGER NOT NULL,
    citation_count INTEGER NOT NULL,
    gap_codes_json TEXT NOT NULL,
    blocking_reason TEXT NOT NULL,
    artifact_ref TEXT NOT NULL,
    lease_owner_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, workspace_id, task_id)
)
"""


class LearningArtifactStoreError(RuntimeError):
    code = "learning_artifact_store_error"


class LearningArtifactConflictError(LearningArtifactStoreError):
    code = "learning_artifact_contract_conflict"


class LearningArtifactNotFoundError(LearningArtifactStoreError, KeyError):
    code = "learning_artifact_not_found"


class LearningCheckpointConflictError(LearningArtifactStoreError):
    code = "learning_resume_checkpoint_conflict"


class LearningFencingConflictError(LearningArtifactStoreError):
    code = "learning_resume_fencing_conflict"


class LearningResumeConflictError(LearningArtifactStoreError):
    code = "learning_resume_revision_conflict"


class LearningResumeNotFoundError(LearningArtifactStoreError, KeyError):
    code = "learning_resume_not_found"


@dataclass(frozen=True, slots=True)
class StoredLearningCitation:
    evidence_ref: str
    title: str
    url: str
    content_hash: str
    fetched_at: str
    page_revision: str
    source_revision: str


@dataclass(frozen=True, slots=True)
class StoredLearningArtifact:
    artifact_id: str
    artifact_ref: str
    kind: str
    task_id: str
    task_revision: int
    plan_id: str
    content_hash: str
    media_type: str
    status: str
    evidence_refs: tuple[str, ...]
    source_revisions: tuple[str, ...]
    citations: tuple[StoredLearningCitation, ...]
    retention: str
    content: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class LearningArtifactSummary:
    artifact_id: str
    kind: str
    content_hash: str
    media_type: str
    status: str
    citation_count: int
    source_revisions: tuple[str, ...]
    retention: str


@dataclass(frozen=True, slots=True)
class LearningCheckpoint:
    task_id: str
    task_revision: int
    plan_id: str
    plan_hash: str
    dag_hash: str
    source_policy_revision: str
    capability_revision: str
    checkpoint_revision: int
    stage: LearningCheckpointStage
    next_action: str
    evidence_count: int
    citation_count: int
    gap_codes: tuple[str, ...]
    blocking_reason: str
    artifact_ref: str
    lease_owner_id: str
    fencing_token: int
    updated_at: str


@dataclass(frozen=True, slots=True)
class LearningResumeSummary:
    task_id: str
    task_revision: int
    goal_summary: str
    plan_id: str
    plan_hash: str
    dag_hash: str
    stage: LearningCheckpointStage
    evidence_count: int
    citation_count: int
    gap_codes: tuple[str, ...]
    blocking_reason: str
    next_action: str
    artifact_ref: str
    artifact: LearningArtifactSummary | None
    checkpoint_revision: int
    fencing_token: int


class LearningArtifactStore:
    """SQLite canonical store with revision CAS and monotonic writer fencing."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._initialize()

    def begin_execution(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task: LearningTask,
        plan: LearningPlan,
        lease_owner_id: str,
    ) -> LearningCheckpoint:
        _validate_scope(owner_id, workspace_id, task.task_id)
        _validate_plan_binding(task, plan)
        _bounded(lease_owner_id, "lease_owner_id", 256)
        timestamp = _now()
        with self._transaction() as connection:
            existing = self._checkpoint_row(
                connection, owner_id=owner_id, workspace_id=workspace_id, task_id=task.task_id
            )
            if existing is not None:
                checkpoint = _checkpoint(existing)
                _assert_checkpoint_binding(task, plan, checkpoint)
                return checkpoint
            connection.execute(
                """INSERT INTO learning_plans (
                    owner_id, workspace_id, task_id, task_revision, plan_id, plan_hash,
                    source_policy_revision, capability_revision, catalog_revision, dag_hash,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    owner_id,
                    workspace_id,
                    task.task_id,
                    task.task_revision,
                    plan.plan_id,
                    plan.plan_hash,
                    plan.source_policy_revision,
                    plan.capability_revision,
                    plan.catalog_revision,
                    plan.dag_hash,
                    _json(asdict(plan)),
                    timestamp,
                ),
            )
            connection.execute(
                """INSERT INTO learning_checkpoints (
                    owner_id, workspace_id, task_id, task_revision, plan_id, plan_hash,
                    dag_hash, source_policy_revision, capability_revision, checkpoint_revision,
                    stage, next_action, evidence_count, citation_count, gap_codes_json,
                    blocking_reason, artifact_ref, lease_owner_id, fencing_token, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'knowledge_pending', 'read_knowledge',
                    0, 0, '[]', '', '', ?, 1, ?)""",
                (
                    owner_id,
                    workspace_id,
                    task.task_id,
                    task.task_revision,
                    plan.plan_id,
                    plan.plan_hash,
                    plan.dag_hash,
                    plan.source_policy_revision,
                    plan.capability_revision,
                    lease_owner_id,
                    timestamp,
                ),
            )
            row = self._checkpoint_row(
                connection, owner_id=owner_id, workspace_id=workspace_id, task_id=task.task_id
            )
            assert row is not None
            return _checkpoint(row)

    def acquire_lease(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        lease_owner_id: str,
    ) -> LearningCheckpoint:
        _validate_scope(owner_id, workspace_id, task_id)
        _bounded(lease_owner_id, "lease_owner_id", 256)
        with self._transaction() as connection:
            row = self._checkpoint_row(
                connection, owner_id=owner_id, workspace_id=workspace_id, task_id=task_id
            )
            if row is None:
                raise LearningResumeNotFoundError("learning checkpoint not found")
            token = int(row["fencing_token"]) + 1
            connection.execute(
                """UPDATE learning_checkpoints
                   SET lease_owner_id = ?, fencing_token = ?, updated_at = ?
                   WHERE owner_id = ? AND workspace_id = ? AND task_id = ?""",
                (lease_owner_id, token, _now(), owner_id, workspace_id, task_id),
            )
            updated = self._checkpoint_row(
                connection, owner_id=owner_id, workspace_id=workspace_id, task_id=task_id
            )
            assert updated is not None
            return _checkpoint(updated)

    def advance_checkpoint(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        expected_checkpoint_revision: int,
        lease_owner_id: str,
        fencing_token: int,
        stage: LearningCheckpointStage,
        next_action: str,
        evidence_count: int = 0,
        citation_count: int = 0,
        gap_codes: Sequence[str] = (),
        blocking_reason: str = "",
        artifact_ref: str = "",
    ) -> LearningCheckpoint:
        _validate_scope(owner_id, workspace_id, task_id)
        if stage not in _STAGES:
            raise ValueError("unsupported Learning checkpoint stage")
        if min(expected_checkpoint_revision, fencing_token) < 1:
            raise ValueError("checkpoint revision and fencing token must be positive")
        if min(evidence_count, citation_count) < 0:
            raise ValueError("checkpoint counts must be non-negative")
        _bounded(next_action, "next_action", 160)
        gaps = tuple(dict.fromkeys(_bounded(item, "gap_code", 160) for item in gap_codes))
        with self._transaction() as connection:
            row = self._checkpoint_row(
                connection, owner_id=owner_id, workspace_id=workspace_id, task_id=task_id
            )
            if row is None:
                raise LearningResumeNotFoundError("learning checkpoint not found")
            if (
                str(row["lease_owner_id"]) != lease_owner_id
                or int(row["fencing_token"]) != fencing_token
            ):
                raise LearningFencingConflictError("stale Learning checkpoint writer")
            if int(row["checkpoint_revision"]) != expected_checkpoint_revision:
                raise LearningCheckpointConflictError("Learning checkpoint revision changed")
            updated_revision = expected_checkpoint_revision + 1
            cursor = connection.execute(
                """UPDATE learning_checkpoints SET checkpoint_revision = ?, stage = ?,
                   next_action = ?, evidence_count = ?, citation_count = ?, gap_codes_json = ?,
                   blocking_reason = ?, artifact_ref = ?, updated_at = ?
                   WHERE owner_id = ? AND workspace_id = ? AND task_id = ?
                     AND checkpoint_revision = ? AND lease_owner_id = ? AND fencing_token = ?""",
                (
                    updated_revision,
                    stage,
                    next_action,
                    evidence_count,
                    citation_count,
                    _json(gaps),
                    blocking_reason[:500],
                    artifact_ref[:1_000],
                    _now(),
                    owner_id,
                    workspace_id,
                    task_id,
                    expected_checkpoint_revision,
                    lease_owner_id,
                    fencing_token,
                ),
            )
            if cursor.rowcount != 1:
                raise LearningCheckpointConflictError("Learning checkpoint CAS failed")
            updated = self._checkpoint_row(
                connection, owner_id=owner_id, workspace_id=workspace_id, task_id=task_id
            )
            assert updated is not None
            return _checkpoint(updated)

    def save_artifact(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task: LearningTask,
        plan: LearningPlan,
        artifact: LearningMapArtifact,
        citations: Sequence[object],
        idempotency_key: str,
        retention: str,
    ) -> StoredLearningArtifact:
        _validate_scope(owner_id, workspace_id, task.task_id)
        _validate_plan_binding(task, plan)
        key = _bounded(idempotency_key, "idempotency_key", 300)
        retention = _bounded(retention, "retention", 80)
        key_hash = _sha256(key)
        with self._transaction() as connection:
            existing = connection.execute(
                """SELECT * FROM learning_artifacts WHERE owner_id = ? AND workspace_id = ?
                   AND task_id = ? AND task_revision = ? AND idempotency_key_hash = ?""",
                (owner_id, workspace_id, task.task_id, task.task_revision, key_hash),
            ).fetchone()
            if existing is not None:
                stored = _artifact(existing)
                if (
                    stored.plan_id != plan.plan_id
                    or stored.content_hash != artifact.content_hash
                    or stored.content != artifact.content
                    or stored.status != artifact.status
                ):
                    raise LearningArtifactConflictError(
                        "Learning Artifact idempotency binding changed"
                    )
                return stored
            expected_hash = "sha256:" + hashlib.sha256(artifact.content.encode()).hexdigest()
            if artifact.content_hash != expected_hash:
                raise ValueError("Learning Artifact content hash is invalid")
            identity = _sha256(
                "\0".join(
                    (
                        owner_id,
                        workspace_id,
                        task.task_id,
                        str(task.task_revision),
                        plan.plan_id,
                        key_hash,
                    )
                )
            )
            artifact_id = f"lart_{identity[:24]}"
            artifact_ref = f"sage://learning/artifacts/{artifact_id}"
            timestamp = _now()
            citation_payload = [_citation_payload(item) for item in citations]
            connection.execute(
                """INSERT INTO learning_artifacts (
                    owner_id, workspace_id, artifact_id, artifact_ref, task_id, task_revision,
                    plan_id, kind, content_hash, media_type, status, evidence_refs_json,
                    source_revisions_json, citations_json, idempotency_key_hash, retention,
                    content, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    owner_id,
                    workspace_id,
                    artifact_id,
                    artifact_ref,
                    task.task_id,
                    task.task_revision,
                    plan.plan_id,
                    artifact.kind,
                    artifact.content_hash,
                    artifact.media_type,
                    artifact.status,
                    _json(artifact.evidence_refs),
                    _json(artifact.source_revisions),
                    _json(citation_payload),
                    key_hash,
                    retention,
                    artifact.content,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM learning_artifacts WHERE artifact_ref = ?", (artifact_ref,)
            ).fetchone()
            assert row is not None
            return _artifact(row)

    def read_artifact(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        artifact_ref: str,
    ) -> StoredLearningArtifact:
        _validate_scope(owner_id, workspace_id, "artifact")
        _artifact_id(artifact_ref)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM learning_artifacts
                   WHERE owner_id = ? AND workspace_id = ? AND artifact_ref = ?""",
                (owner_id, workspace_id, artifact_ref),
            ).fetchone()
        if row is None:
            raise LearningArtifactNotFoundError("Learning Artifact not found")
        return _artifact(row)

    def load_plan(self, *, owner_id: str, workspace_id: str, task_id: str) -> LearningPlan:
        _validate_scope(owner_id, workspace_id, task_id)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT payload_json FROM learning_plans
                   WHERE owner_id = ? AND workspace_id = ? AND task_id = ?""",
                (owner_id, workspace_id, task_id),
            ).fetchone()
        if row is None:
            raise LearningResumeNotFoundError("Learning plan not found")
        return _plan(json.loads(str(row["payload_json"])))

    def checkpoint(self, *, owner_id: str, workspace_id: str, task_id: str) -> LearningCheckpoint:
        with self._connect() as connection:
            row = self._checkpoint_row(
                connection, owner_id=owner_id, workspace_id=workspace_id, task_id=task_id
            )
        if row is None:
            raise LearningResumeNotFoundError("Learning checkpoint not found")
        return _checkpoint(row)

    def resume(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task: LearningTask,
        capability_revision: str,
    ) -> LearningResumeSummary:
        _validate_scope(owner_id, workspace_id, task.task_id)
        try:
            plan = self.load_plan(
                owner_id=owner_id, workspace_id=workspace_id, task_id=task.task_id
            )
            checkpoint = self.checkpoint(
                owner_id=owner_id, workspace_id=workspace_id, task_id=task.task_id
            )
        except LearningResumeNotFoundError:
            raise
        if (
            task.status != "active"
            or task.workspace_id != workspace_id
            or plan.task_revision != task.task_revision
            or checkpoint.task_revision != task.task_revision
            or checkpoint.plan_id != plan.plan_id
            or checkpoint.plan_hash != plan.plan_hash
            or checkpoint.dag_hash != plan.dag_hash
            or checkpoint.source_policy_revision != source_policy_revision(task.source_policy)
            or checkpoint.capability_revision != capability_revision
            or plan.capability_revision != capability_revision
        ):
            raise LearningResumeConflictError("Learning resume revision binding changed")
        artifact_summary = None
        if checkpoint.artifact_ref:
            artifact = self.read_artifact(
                owner_id=owner_id,
                workspace_id=workspace_id,
                artifact_ref=checkpoint.artifact_ref,
            )
            artifact_summary = LearningArtifactSummary(
                artifact_id=artifact.artifact_id,
                kind=artifact.kind,
                content_hash=artifact.content_hash,
                media_type=artifact.media_type,
                status=artifact.status,
                citation_count=len(artifact.citations),
                source_revisions=artifact.source_revisions,
                retention=artifact.retention,
            )
        return LearningResumeSummary(
            task_id=task.task_id,
            task_revision=task.task_revision,
            goal_summary=task.topic,
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            dag_hash=plan.dag_hash,
            stage=checkpoint.stage,
            evidence_count=checkpoint.evidence_count,
            citation_count=checkpoint.citation_count,
            gap_codes=checkpoint.gap_codes,
            blocking_reason=checkpoint.blocking_reason,
            next_action=checkpoint.next_action,
            artifact_ref=checkpoint.artifact_ref,
            artifact=artifact_summary,
            checkpoint_revision=checkpoint.checkpoint_revision,
            fencing_token=checkpoint.fencing_token,
        )

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(_PLAN_TABLE)
            connection.execute(_ARTIFACT_TABLE)
            connection.execute(_CHECKPOINT_TABLE)
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _transaction(self) -> _ImmediateTransaction:
        return _ImmediateTransaction(self)

    @staticmethod
    def _checkpoint_row(
        connection: sqlite3.Connection,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
    ) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            connection.execute(
                """SELECT * FROM learning_checkpoints
               WHERE owner_id = ? AND workspace_id = ? AND task_id = ?""",
                (owner_id, workspace_id, task_id),
            ).fetchone(),
        )


class _ImmediateTransaction:
    def __init__(self, store: LearningArtifactStore) -> None:
        self.store = store
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self.store._lock.acquire()
        self.connection = self.store._connect()
        self.connection.execute("BEGIN IMMEDIATE")
        return self.connection

    def __exit__(self, exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
        assert self.connection is not None
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()
            self.store._lock.release()


_STAGES = frozenset(cast(tuple[str, ...], get_args(LearningCheckpointStage)))


def _checkpoint(row: sqlite3.Row) -> LearningCheckpoint:
    return LearningCheckpoint(
        task_id=str(row["task_id"]),
        task_revision=int(row["task_revision"]),
        plan_id=str(row["plan_id"]),
        plan_hash=str(row["plan_hash"]),
        dag_hash=str(row["dag_hash"]),
        source_policy_revision=str(row["source_policy_revision"]),
        capability_revision=str(row["capability_revision"]),
        checkpoint_revision=int(row["checkpoint_revision"]),
        stage=cast(LearningCheckpointStage, row["stage"]),
        next_action=str(row["next_action"]),
        evidence_count=int(row["evidence_count"]),
        citation_count=int(row["citation_count"]),
        gap_codes=tuple(json.loads(str(row["gap_codes_json"]))),
        blocking_reason=str(row["blocking_reason"]),
        artifact_ref=str(row["artifact_ref"]),
        lease_owner_id=str(row["lease_owner_id"]),
        fencing_token=int(row["fencing_token"]),
        updated_at=str(row["updated_at"]),
    )


def _artifact(row: sqlite3.Row) -> StoredLearningArtifact:
    return StoredLearningArtifact(
        artifact_id=str(row["artifact_id"]),
        artifact_ref=str(row["artifact_ref"]),
        kind=str(row["kind"]),
        task_id=str(row["task_id"]),
        task_revision=int(row["task_revision"]),
        plan_id=str(row["plan_id"]),
        content_hash=str(row["content_hash"]),
        media_type=str(row["media_type"]),
        status=str(row["status"]),
        evidence_refs=tuple(json.loads(str(row["evidence_refs_json"]))),
        source_revisions=tuple(json.loads(str(row["source_revisions_json"]))),
        citations=tuple(
            StoredLearningCitation(**item) for item in json.loads(str(row["citations_json"]))
        ),
        retention=str(row["retention"]),
        content=str(row["content"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _citation_payload(item: object) -> dict[str, str]:
    evidence_ref = str(
        getattr(item, "citation_id", None) or getattr(item, "evidence_ref", "")
    ).strip()
    if not evidence_ref:
        raise ValueError("Learning Artifact citation requires an evidence ref")
    return {
        "evidence_ref": evidence_ref,
        "title": str(getattr(item, "title", ""))[:300],
        "url": str(getattr(item, "url", ""))[:2_000],
        "content_hash": str(getattr(item, "content_hash", ""))[:160],
        "fetched_at": str(getattr(item, "fetched_at", ""))[:80],
        "page_revision": str(getattr(item, "page_revision", ""))[:160],
        "source_revision": str(getattr(item, "source_revision", ""))[:160],
    }


def _plan(data: dict[str, object]) -> LearningPlan:
    raw_policy = cast(dict[str, object], data["source_policy"])
    raw_units = cast(list[dict[str, object]], data["units"])
    return LearningPlan(
        schema_version=int(str(data["schema_version"])),
        plan_id=str(data["plan_id"]),
        plan_hash=str(data["plan_hash"]),
        plan_revision=int(str(data["plan_revision"])),
        workspace_id=str(data["workspace_id"]),
        task_id=str(data["task_id"]),
        task_revision=int(str(data["task_revision"])),
        goal_id=str(data["goal_id"]),
        goal_revision=str(data["goal_revision"]),
        source_policy=LearningSourcePolicy(
            knowledge=cast(str, raw_policy["knowledge"]),  # type: ignore[arg-type]
            web=cast(str, raw_policy["web"]),  # type: ignore[arg-type]
            domains=tuple(cast(list[str], raw_policy["domains"])),
            freshness=cast(str, raw_policy["freshness"]),  # type: ignore[arg-type]
        ),
        source_policy_revision=str(data["source_policy_revision"]),
        capability_revision=str(data["capability_revision"]),
        catalog_revision=str(data["catalog_revision"]),
        dag_id=str(data["dag_id"]),
        dag_hash=str(data["dag_hash"]),
        units=tuple(
            KnowledgeUnit(
                unit_id=str(item["unit_id"]),
                ordinal=int(str(item["ordinal"])),
                title=str(item["title"]),
                objective=str(item["objective"]),
                prerequisite_unit_ids=tuple(cast(list[str], item["prerequisite_unit_ids"])),
                source_policy_revision=str(item["source_policy_revision"]),
                risk_class=str(item["risk_class"]),
                status=cast(LearningUnitStatus, item["status"]),
                evidence_refs=tuple(cast(list[str], item["evidence_refs"])),
                source_revisions=tuple(cast(list[str], item["source_revisions"])),
            )
            for item in raw_units
        ),
    )


def _validate_plan_binding(task: LearningTask, plan: LearningPlan) -> None:
    if (
        task.status != "active"
        or task.workspace_id != plan.workspace_id
        or task.task_id != plan.task_id
        or task.task_revision != plan.task_revision
        or source_policy_revision(task.source_policy) != plan.source_policy_revision
        or task.learning_goal_ref is None
        or task.learning_goal_ref.get("goal_id") != plan.goal_id
        or task.learning_goal_ref.get("goal_revision") != plan.goal_revision
    ):
        raise LearningResumeConflictError("Learning plan binding changed")


def _assert_checkpoint_binding(
    task: LearningTask, plan: LearningPlan, checkpoint: LearningCheckpoint
) -> None:
    if (
        checkpoint.task_revision != task.task_revision
        or checkpoint.plan_id != plan.plan_id
        or checkpoint.plan_hash != plan.plan_hash
        or checkpoint.dag_hash != plan.dag_hash
        or checkpoint.source_policy_revision != plan.source_policy_revision
        or checkpoint.capability_revision != plan.capability_revision
    ):
        raise LearningResumeConflictError("Learning checkpoint binding changed")


def _artifact_id(artifact_ref: str) -> str:
    parsed = urlsplit(artifact_ref)
    if parsed.scheme != "sage" or parsed.netloc != "learning":
        raise ValueError("invalid Learning Artifact ref")
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or parts[0] != "artifacts" or not parts[1].startswith("lart_"):
        raise ValueError("invalid Learning Artifact ref")
    return parts[1]


def _validate_scope(owner_id: str, workspace_id: str, resource_id: str) -> None:
    _bounded(owner_id, "owner_id", 256)
    _bounded(workspace_id, "workspace_id", 256)
    _bounded(resource_id, "resource_id", 256)


def _bounded(value: str, field: str, maximum: int) -> str:
    normalized = str(value).strip()
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field} must be non-empty and bounded")
    return normalized


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "LearningArtifactConflictError",
    "LearningArtifactNotFoundError",
    "LearningArtifactStore",
    "LearningArtifactStoreError",
    "LearningArtifactSummary",
    "LearningCheckpoint",
    "LearningCheckpointConflictError",
    "LearningCheckpointStage",
    "LearningFencingConflictError",
    "LearningResumeConflictError",
    "LearningResumeNotFoundError",
    "LearningResumeSummary",
    "StoredLearningArtifact",
    "StoredLearningCitation",
]
