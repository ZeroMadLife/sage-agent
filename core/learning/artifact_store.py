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
from core.learning.research import LearningResearchEvidence, LearningResearchReceipt
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
    schema_version INTEGER NOT NULL,
    goal_id TEXT NOT NULL,
    goal_revision TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    plan_revision INTEGER NOT NULL,
    unit_ids_json TEXT NOT NULL,
    kind TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    media_type TEXT NOT NULL,
    status TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    source_revisions_json TEXT NOT NULL,
    citations_json TEXT NOT NULL,
    idempotency_key_hash TEXT NOT NULL,
    retention TEXT NOT NULL,
    research_receipt_ref TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, workspace_id, artifact_id),
    UNIQUE (owner_id, workspace_id, task_id, task_revision, idempotency_key_hash),
    UNIQUE (artifact_ref)
)
"""

_RESEARCH_RECEIPT_TABLE = """
CREATE TABLE IF NOT EXISTS learning_research_receipts (
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    receipt_id TEXT NOT NULL,
    receipt_ref TEXT NOT NULL,
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL,
    plan_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, workspace_id, receipt_id),
    UNIQUE (receipt_ref)
)
"""

_ADVANCE_REQUEST_TABLE = """
CREATE TABLE IF NOT EXISTS learning_advance_requests (
    owner_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    request_key_hash TEXT NOT NULL,
    expected_checkpoint_revision INTEGER NOT NULL,
    request_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    response_digest TEXT NOT NULL,
    response_json TEXT NOT NULL,
    error_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (owner_id, workspace_id, task_id, request_key_hash)
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
    last_advance_key_hash TEXT NOT NULL DEFAULT '',
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
    schema_version: int
    kind: str
    task_id: str
    task_revision: int
    goal_id: str
    goal_revision: str
    plan_id: str
    plan_revision: int
    unit_ids: tuple[str, ...]
    content_hash: str
    media_type: str
    status: str
    evidence_refs: tuple[str, ...]
    source_revisions: tuple[str, ...]
    citations: tuple[StoredLearningCitation, ...]
    retention: str
    research_receipt_ref: str
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
class StoredLearningResearchReceipt:
    receipt_ref: str
    receipt: LearningResearchReceipt
    created_at: str


@dataclass(frozen=True, slots=True)
class LearningAdvanceClaim:
    request_key_hash: str
    request_digest: str
    checkpoint: LearningCheckpoint | None
    plan: LearningPlan | None
    replay: LearningResumeSummary | None


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
        idempotency_key: str = "",
        artifact_ref: str = "",
        evidence_count: int = 0,
        citation_count: int = 0,
        gap_codes: Sequence[str] = (),
    ) -> LearningCheckpoint:
        _validate_scope(owner_id, workspace_id, task.task_id)
        _validate_plan_binding(task, plan)
        _bounded(lease_owner_id, "lease_owner_id", 256)
        if min(evidence_count, citation_count) < 0:
            raise ValueError("checkpoint counts must be non-negative")
        gaps = tuple(dict.fromkeys(_bounded(item, "gap_code", 160) for item in gap_codes))
        advance_key_hash = (
            _sha256(_bounded(idempotency_key, "idempotency_key", 300)) if idempotency_key else ""
        )
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
                    blocking_reason, artifact_ref, lease_owner_id, fencing_token,
                    last_advance_key_hash, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'knowledge_pending', 'read_knowledge',
                    ?, ?, ?, '', ?, ?, 1, ?, ?)""",
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
                    evidence_count,
                    citation_count,
                    _json(gaps),
                    artifact_ref[:1_000],
                    lease_owner_id,
                    advance_key_hash,
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
        idempotency_key: str = "",
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
        advance_key_hash = (
            _sha256(_bounded(idempotency_key, "idempotency_key", 300)) if idempotency_key else ""
        )
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
                   blocking_reason = ?, artifact_ref = ?, last_advance_key_hash = ?, updated_at = ?
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
                    advance_key_hash,
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

    def is_advance_replay(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        idempotency_key: str,
    ) -> bool:
        _validate_scope(owner_id, workspace_id, task_id)
        key_hash = _sha256(_bounded(idempotency_key, "idempotency_key", 300))
        with self._connect() as connection:
            row = self._checkpoint_row(
                connection, owner_id=owner_id, workspace_id=workspace_id, task_id=task_id
            )
        return row is not None and str(row["last_advance_key_hash"]) == key_hash

    def claim_advance_request(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task: LearningTask,
        capability_revision: str,
        catalog_revision: str,
        expected_checkpoint_revision: int,
        idempotency_key: str,
    ) -> LearningAdvanceClaim:
        _validate_scope(owner_id, workspace_id, task.task_id)
        if expected_checkpoint_revision < 0:
            raise ValueError("expected checkpoint revision must be non-negative")
        key_hash = _sha256(_bounded(idempotency_key, "idempotency_key", 300))
        capability_revision = _bounded(capability_revision, "capability_revision", 256)
        catalog_revision = _bounded(catalog_revision, "catalog_revision", 256)
        request_digest = "sha256:" + _sha256(
            _json(
                {
                    "owner_id": owner_id,
                    "workspace_id": workspace_id,
                    "task_id": task.task_id,
                    "task_revision": task.task_revision,
                    "source_policy_revision": source_policy_revision(task.source_policy),
                    "capability_revision": capability_revision,
                    "catalog_revision": catalog_revision,
                    "expected_checkpoint_revision": expected_checkpoint_revision,
                    "request_key_hash": key_hash,
                }
            )
        )
        with self._transaction() as connection:
            existing = connection.execute(
                """SELECT * FROM learning_advance_requests
                   WHERE owner_id = ? AND workspace_id = ? AND task_id = ?
                     AND request_key_hash = ?""",
                (owner_id, workspace_id, task.task_id, key_hash),
            ).fetchone()
            if existing is not None:
                if (
                    int(existing["expected_checkpoint_revision"]) != expected_checkpoint_revision
                    or str(existing["request_digest"]) != request_digest
                ):
                    raise LearningCheckpointConflictError(
                        "Learning advance request binding changed"
                    )
                if str(existing["status"]) == "succeeded":
                    payload = json.loads(str(existing["response_json"]))
                    return LearningAdvanceClaim(
                        request_key_hash=key_hash,
                        request_digest=request_digest,
                        checkpoint=None,
                        plan=None,
                        replay=_resume_summary(payload),
                    )
                raise LearningCheckpointConflictError("Learning advance request is already running")

            running = connection.execute(
                """SELECT 1 FROM learning_advance_requests
                   WHERE owner_id = ? AND workspace_id = ? AND task_id = ?
                     AND status = 'running' LIMIT 1""",
                (owner_id, workspace_id, task.task_id),
            ).fetchone()
            if running is not None:
                raise LearningCheckpointConflictError("Learning advance request is already running")

            _validate_task_for_advance(task, workspace_id=workspace_id)
            checkpoint_row = self._checkpoint_row(
                connection,
                owner_id=owner_id,
                workspace_id=workspace_id,
                task_id=task.task_id,
            )
            checkpoint = _checkpoint(checkpoint_row) if checkpoint_row is not None else None
            plan: LearningPlan | None = None
            if checkpoint is None:
                if expected_checkpoint_revision != 0:
                    raise LearningCheckpointConflictError("Learning checkpoint revision changed")
            else:
                plan_row = connection.execute(
                    """SELECT payload_json FROM learning_plans
                       WHERE owner_id = ? AND workspace_id = ? AND task_id = ?""",
                    (owner_id, workspace_id, task.task_id),
                ).fetchone()
                if plan_row is None:
                    raise LearningResumeConflictError("Learning plan is missing")
                plan = _plan(json.loads(str(plan_row["payload_json"])))
                _validate_plan_binding(task, plan)
                _assert_checkpoint_binding(task, plan, checkpoint)
                if (
                    checkpoint.checkpoint_revision != expected_checkpoint_revision
                    or plan.capability_revision != capability_revision
                    or plan.catalog_revision != catalog_revision
                ):
                    if checkpoint.checkpoint_revision != expected_checkpoint_revision:
                        raise LearningCheckpointConflictError(
                            "Learning checkpoint revision changed"
                        )
                    raise LearningResumeConflictError("Learning execution binding changed")
                fencing_token = checkpoint.fencing_token + 1
                lease_owner_id = f"advance:{key_hash[:48]}"
                connection.execute(
                    """UPDATE learning_checkpoints
                       SET lease_owner_id = ?, fencing_token = ?, updated_at = ?
                       WHERE owner_id = ? AND workspace_id = ? AND task_id = ?
                         AND checkpoint_revision = ? AND fencing_token = ?""",
                    (
                        lease_owner_id,
                        fencing_token,
                        _now(),
                        owner_id,
                        workspace_id,
                        task.task_id,
                        expected_checkpoint_revision,
                        checkpoint.fencing_token,
                    ),
                )
                checkpoint = LearningCheckpoint(
                    task_id=checkpoint.task_id,
                    task_revision=checkpoint.task_revision,
                    plan_id=checkpoint.plan_id,
                    plan_hash=checkpoint.plan_hash,
                    dag_hash=checkpoint.dag_hash,
                    source_policy_revision=checkpoint.source_policy_revision,
                    capability_revision=checkpoint.capability_revision,
                    checkpoint_revision=checkpoint.checkpoint_revision,
                    stage=checkpoint.stage,
                    next_action=checkpoint.next_action,
                    evidence_count=checkpoint.evidence_count,
                    citation_count=checkpoint.citation_count,
                    gap_codes=checkpoint.gap_codes,
                    blocking_reason=checkpoint.blocking_reason,
                    artifact_ref=checkpoint.artifact_ref,
                    lease_owner_id=lease_owner_id,
                    fencing_token=fencing_token,
                    updated_at=checkpoint.updated_at,
                )
            timestamp = _now()
            connection.execute(
                """INSERT INTO learning_advance_requests (
                    owner_id, workspace_id, task_id, request_key_hash,
                    expected_checkpoint_revision, request_digest, status,
                    response_digest, response_json, error_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', '', '', '', ?, ?)""",
                (
                    owner_id,
                    workspace_id,
                    task.task_id,
                    key_hash,
                    expected_checkpoint_revision,
                    request_digest,
                    timestamp,
                    timestamp,
                ),
            )
            return LearningAdvanceClaim(
                request_key_hash=key_hash,
                request_digest=request_digest,
                checkpoint=checkpoint,
                plan=plan,
                replay=None,
            )

    def complete_advance_request(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        claim: LearningAdvanceClaim,
        response: LearningResumeSummary,
    ) -> None:
        payload = _json(asdict(response))
        response_digest = "sha256:" + _sha256(payload)
        with self._transaction() as connection:
            cursor = connection.execute(
                """UPDATE learning_advance_requests
                   SET status = 'succeeded', response_digest = ?, response_json = ?,
                       error_code = '', updated_at = ?
                   WHERE owner_id = ? AND workspace_id = ? AND task_id = ?
                     AND request_key_hash = ? AND request_digest = ? AND status = 'running'""",
                (
                    response_digest,
                    payload,
                    _now(),
                    owner_id,
                    workspace_id,
                    task_id,
                    claim.request_key_hash,
                    claim.request_digest,
                ),
            )
            if cursor.rowcount != 1:
                raise LearningCheckpointConflictError("Learning advance request completion changed")

    def fail_advance_request(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        claim: LearningAdvanceClaim,
        error_code: str,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                """UPDATE learning_advance_requests
                   SET status = 'failed', error_code = ?, updated_at = ?
                   WHERE owner_id = ? AND workspace_id = ? AND task_id = ?
                     AND request_key_hash = ? AND request_digest = ? AND status = 'running'""",
                (
                    error_code[:160],
                    _now(),
                    owner_id,
                    workspace_id,
                    task_id,
                    claim.request_key_hash,
                    claim.request_digest,
                ),
            )

    def save_research_receipt(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        task: LearningTask,
        plan: LearningPlan,
        receipt: LearningResearchReceipt,
    ) -> StoredLearningResearchReceipt:
        _validate_scope(owner_id, workspace_id, task.task_id)
        _validate_plan_binding(task, plan)
        _validate_research_receipt(task, plan, receipt)
        receipt_id = _bounded(receipt.receipt_id, "receipt_id", 160)
        receipt_ref = f"sage://learning/research-receipts/{receipt_id}"
        payload = _json(asdict(receipt))
        with self._transaction() as connection:
            existing = connection.execute(
                """SELECT * FROM learning_research_receipts
                   WHERE owner_id = ? AND workspace_id = ? AND receipt_id = ?""",
                (owner_id, workspace_id, receipt_id),
            ).fetchone()
            if existing is not None:
                if str(existing["payload_json"]) != payload:
                    raise LearningArtifactConflictError(
                        "Learning Research receipt identity changed"
                    )
                return _stored_research_receipt(existing)
            timestamp = _now()
            connection.execute(
                """INSERT INTO learning_research_receipts (
                    owner_id, workspace_id, receipt_id, receipt_ref, task_id,
                    task_revision, plan_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    owner_id,
                    workspace_id,
                    receipt_id,
                    receipt_ref,
                    task.task_id,
                    task.task_revision,
                    plan.plan_id,
                    payload,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM learning_research_receipts WHERE receipt_ref = ?",
                (receipt_ref,),
            ).fetchone()
            assert row is not None
            return _stored_research_receipt(row)

    def read_research_receipt(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        receipt_ref: str,
    ) -> StoredLearningResearchReceipt:
        _validate_scope(owner_id, workspace_id, "research_receipt")
        _research_receipt_id(receipt_ref)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM learning_research_receipts
                   WHERE owner_id = ? AND workspace_id = ? AND receipt_ref = ?""",
                (owner_id, workspace_id, receipt_ref),
            ).fetchone()
        if row is None:
            raise LearningResumeNotFoundError("Learning Research receipt not found")
        return _stored_research_receipt(row)

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
        research_receipt_ref: str = "",
    ) -> StoredLearningArtifact:
        _validate_scope(owner_id, workspace_id, task.task_id)
        _validate_plan_binding(task, plan)
        key = _bounded(idempotency_key, "idempotency_key", 300)
        retention = _bounded(retention, "retention", 80)
        citation_payload = tuple(_citation_payload(item) for item in citations)
        _validate_artifact_binding(plan, artifact, citation_payload)
        if research_receipt_ref:
            _research_receipt_id(research_receipt_ref)
        key_hash = _sha256(key)
        with self._transaction() as connection:
            if research_receipt_ref:
                receipt_row = connection.execute(
                    """SELECT task_id, task_revision, plan_id, payload_json
                       FROM learning_research_receipts
                       WHERE owner_id = ? AND workspace_id = ? AND receipt_ref = ?""",
                    (owner_id, workspace_id, research_receipt_ref),
                ).fetchone()
                if (
                    receipt_row is None
                    or str(receipt_row["task_id"]) != task.task_id
                    or int(receipt_row["task_revision"]) != task.task_revision
                    or str(receipt_row["plan_id"]) != plan.plan_id
                ):
                    raise LearningArtifactConflictError(
                        "Learning Artifact Research receipt binding changed"
                    )
                receipt_payload = json.loads(str(receipt_row["payload_json"]))
                receipt_evidence_refs = tuple(
                    str(item.get("evidence_ref", ""))
                    for item in receipt_payload.get("evidence", ())
                )
                if receipt_evidence_refs != artifact.evidence_refs:
                    raise LearningArtifactConflictError(
                        "Learning Artifact Research evidence binding changed"
                    )
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
                    or stored.research_receipt_ref != research_receipt_ref
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
            connection.execute(
                """INSERT INTO learning_artifacts (
                    owner_id, workspace_id, artifact_id, artifact_ref, task_id, task_revision,
                    schema_version, goal_id, goal_revision, plan_id, plan_revision, unit_ids_json,
                    kind, content_hash, media_type, status, evidence_refs_json,
                    source_revisions_json, citations_json, idempotency_key_hash, retention,
                    research_receipt_ref, content, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    owner_id,
                    workspace_id,
                    artifact_id,
                    artifact_ref,
                    task.task_id,
                    task.task_revision,
                    artifact.schema_version,
                    plan.goal_id,
                    plan.goal_revision,
                    plan.plan_id,
                    plan.plan_revision,
                    _json(artifact.unit_ids),
                    artifact.kind,
                    artifact.content_hash,
                    artifact.media_type,
                    artifact.status,
                    _json(artifact.evidence_refs),
                    _json(artifact.source_revisions),
                    _json(citation_payload),
                    key_hash,
                    retention,
                    research_receipt_ref,
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
            connection.execute(_RESEARCH_RECEIPT_TABLE)
            connection.execute(_ADVANCE_REQUEST_TABLE)
            connection.execute(_CHECKPOINT_TABLE)
            artifact_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(learning_artifacts)")
            }
            artifact_expansions = {
                "schema_version": "INTEGER NOT NULL DEFAULT 1",
                "goal_id": "TEXT NOT NULL DEFAULT ''",
                "goal_revision": "TEXT NOT NULL DEFAULT ''",
                "plan_revision": "INTEGER NOT NULL DEFAULT 1",
                "unit_ids_json": "TEXT NOT NULL DEFAULT '[]'",
                "research_receipt_ref": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in artifact_expansions.items():
                if name not in artifact_columns:
                    connection.execute(
                        f"ALTER TABLE learning_artifacts ADD COLUMN {name} {declaration}"
                    )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(learning_checkpoints)")
            }
            if "last_advance_key_hash" not in columns:
                connection.execute(
                    "ALTER TABLE learning_checkpoints ADD COLUMN "
                    "last_advance_key_hash TEXT NOT NULL DEFAULT ''"
                )
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
        schema_version=int(row["schema_version"]),
        kind=str(row["kind"]),
        task_id=str(row["task_id"]),
        task_revision=int(row["task_revision"]),
        goal_id=str(row["goal_id"]),
        goal_revision=str(row["goal_revision"]),
        plan_id=str(row["plan_id"]),
        plan_revision=int(row["plan_revision"]),
        unit_ids=tuple(json.loads(str(row["unit_ids_json"]))),
        content_hash=str(row["content_hash"]),
        media_type=str(row["media_type"]),
        status=str(row["status"]),
        evidence_refs=tuple(json.loads(str(row["evidence_refs_json"]))),
        source_revisions=tuple(json.loads(str(row["source_revisions_json"]))),
        citations=tuple(
            StoredLearningCitation(**item) for item in json.loads(str(row["citations_json"]))
        ),
        retention=str(row["retention"]),
        research_receipt_ref=str(row["research_receipt_ref"]),
        content=str(row["content"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _stored_research_receipt(row: sqlite3.Row) -> StoredLearningResearchReceipt:
    payload = json.loads(str(row["payload_json"]))
    payload["allowed_domains"] = tuple(payload.get("allowed_domains", ()))
    payload["evidence"] = tuple(
        LearningResearchEvidence(**item) for item in payload.get("evidence", ())
    )
    return StoredLearningResearchReceipt(
        receipt_ref=str(row["receipt_ref"]),
        receipt=LearningResearchReceipt(**payload),
        created_at=str(row["created_at"]),
    )


def _resume_summary(data: dict[str, object]) -> LearningResumeSummary:
    raw_artifact = data.get("artifact")
    artifact = None
    if isinstance(raw_artifact, dict):
        artifact = LearningArtifactSummary(
            artifact_id=str(raw_artifact["artifact_id"]),
            kind=str(raw_artifact["kind"]),
            content_hash=str(raw_artifact["content_hash"]),
            media_type=str(raw_artifact["media_type"]),
            status=str(raw_artifact["status"]),
            citation_count=int(str(raw_artifact["citation_count"])),
            source_revisions=tuple(cast(list[str], raw_artifact["source_revisions"])),
            retention=str(raw_artifact["retention"]),
        )
    return LearningResumeSummary(
        task_id=str(data["task_id"]),
        task_revision=int(str(data["task_revision"])),
        goal_summary=str(data["goal_summary"]),
        plan_id=str(data["plan_id"]),
        plan_hash=str(data["plan_hash"]),
        dag_hash=str(data["dag_hash"]),
        stage=cast(LearningCheckpointStage, data["stage"]),
        evidence_count=int(str(data["evidence_count"])),
        citation_count=int(str(data["citation_count"])),
        gap_codes=tuple(cast(list[str], data["gap_codes"])),
        blocking_reason=str(data["blocking_reason"]),
        next_action=str(data["next_action"]),
        artifact_ref=str(data["artifact_ref"]),
        artifact=artifact,
        checkpoint_revision=int(str(data["checkpoint_revision"])),
        fencing_token=int(str(data["fencing_token"])),
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


def _validate_artifact_binding(
    plan: LearningPlan,
    artifact: LearningMapArtifact,
    citations: tuple[dict[str, str], ...],
) -> None:
    evidence_refs = tuple(item["evidence_ref"] for item in citations)
    source_revisions = tuple(sorted({item["source_revision"] for item in citations}))
    if any(
        not item["content_hash"] or not item["page_revision"] or not item["source_revision"]
        for item in citations
    ):
        raise ValueError("Learning Artifact citations require revision and content hash")
    if (
        artifact.schema_version != 1
        or artifact.plan_id != plan.plan_id
        or artifact.unit_ids != tuple(unit.unit_id for unit in plan.units)
        or artifact.kind != "learning_map"
        or artifact.media_type != "text/markdown"
    ):
        raise ValueError("Learning Artifact identity binding is invalid")
    if artifact.evidence_refs != evidence_refs:
        raise ValueError("Learning Artifact evidence refs do not match citations")
    if artifact.source_revisions != source_revisions:
        raise ValueError("Learning Artifact source revisions do not match citations")
    if artifact.citation_count != len(citations):
        raise ValueError("Learning Artifact citation count does not match citations")
    if not citations and artifact.status not in {"source_gap", "unverified", "blocked"}:
        raise ValueError("Learning Artifact without citations cannot be ready")


def _validate_research_receipt(
    task: LearningTask,
    plan: LearningPlan,
    receipt: LearningResearchReceipt,
) -> None:
    if (
        receipt.schema_version != 1
        or receipt.task_id != task.task_id
        or receipt.task_revision != task.task_revision
        or receipt.plan_id != plan.plan_id
        or receipt.plan_revision != plan.plan_revision
        or receipt.unit_id not in {unit.unit_id for unit in plan.units}
        or receipt.capability_revision != plan.capability_revision
        or receipt.source_policy_revision != plan.source_policy_revision
        or receipt.allowed_domains != task.source_policy.domains
        or receipt.freshness != task.source_policy.freshness
        or receipt.risk_decision != task.risk_class
        or receipt.token_budget < 1
        or receipt.max_steps < 1
        or receipt.timeout_seconds <= 0
        or receipt.actual_token_usage < 0
        or receipt.actual_token_usage > receipt.token_budget
        or receipt.actual_tool_count < 0
    ):
        raise LearningResumeConflictError("Learning Research receipt binding changed")


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


def _validate_task_for_advance(task: LearningTask, *, workspace_id: str) -> None:
    if (
        task.status != "active"
        or task.workspace_id != workspace_id
        or task.learning_goal_ref is None
        or not str(task.learning_goal_ref.get("goal_id", "")).strip()
        or not str(task.learning_goal_ref.get("goal_revision", "")).strip()
    ):
        raise LearningResumeConflictError("Learning task binding changed")


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


def _research_receipt_id(receipt_ref: str) -> str:
    parsed = urlsplit(receipt_ref)
    if parsed.scheme != "sage" or parsed.netloc != "learning":
        raise ValueError("invalid Learning Research receipt ref")
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or parts[0] != "research-receipts" or not parts[1].startswith("lrsearch_"):
        raise ValueError("invalid Learning Research receipt ref")
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
    "LearningAdvanceClaim",
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
    "StoredLearningResearchReceipt",
]
