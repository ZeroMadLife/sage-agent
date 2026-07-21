"""Deterministic, cross-session Mastery Ledger for validated learning evidence."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Literal

MASTERY_RUBRIC_REVISION = "sage-mastery-v1"
MasteryEvidenceKind = Literal["quiz", "explanation", "code_test", "artifact", "project", "citation"]
MasteryEvidenceResult = Literal["pass", "fail", "partial", "observed"]
MasteryEvidenceStatus = Literal["valid", "invalidated"]

_KINDS = frozenset({"quiz", "explanation", "code_test", "artifact", "project", "citation"})
_RESULTS = frozenset({"pass", "fail", "partial", "observed"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}$")
_MAX_METADATA = 16
_MAX_TEXT = 1_000
_SCHEMA = """
CREATE TABLE IF NOT EXISTS mastery_evidence (
    owner_id TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    learning_goal_id TEXT NOT NULL,
    learning_goal_revision TEXT NOT NULL,
    capability_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('quiz','explanation','code_test','artifact','project','citation')),
    result TEXT NOT NULL CHECK (result IN ('pass','fail','partial','observed')),
    rubric_revision TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    source_evidence_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('valid','invalidated')),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    invalidated_at TEXT,
    invalidation_reason TEXT,
    PRIMARY KEY (owner_id, evidence_id)
);
CREATE INDEX IF NOT EXISTS mastery_evidence_scope_idx
ON mastery_evidence(owner_id, workspace_id, learning_goal_id, learning_goal_revision, capability_id, created_at);
CREATE INDEX IF NOT EXISTS mastery_evidence_source_idx
ON mastery_evidence(source_ref, source_evidence_id);
"""

_RELIABILITY: dict[str, float] = {
    "code_test": 1.0,
    "project": 1.0,
    "quiz": 0.9,
    "artifact": 0.75,
    "explanation": 0.6,
    "citation": 0.3,
}
_RESULT_SCORE: dict[str, float] = {
    "pass": 1.0,
    "fail": 0.0,
    "partial": 0.5,
    "observed": 0.25,
}


class MasteryLedgerError(RuntimeError):
    """Base error for Ledger persistence and projection failures."""


class MasteryLedgerConflictError(MasteryLedgerError):
    """A duplicate evidence ID or stale invalidation revision was supplied."""


class MasteryLedgerNotFoundError(MasteryLedgerError):
    """The requested evidence does not exist in the requested workspace."""


@dataclass(frozen=True, slots=True)
class MasteryCapability:
    capability_id: str
    label: str
    weight: float
    required: bool = True


@dataclass(frozen=True, slots=True)
class MasteryEvidenceInput:
    owner_id: str
    evidence_id: str
    workspace_id: str
    learning_goal_id: str
    learning_goal_revision: str
    capability_id: str
    kind: MasteryEvidenceKind
    result: MasteryEvidenceResult
    source_ref: str
    source_evidence_id: str
    session_id: str
    run_id: str
    summary: str
    metadata: Mapping[str, object] = field(default_factory=dict)
    created_at: str | None = None


@dataclass(frozen=True, slots=True)
class MasteryEvidence:
    owner_id: str
    evidence_id: str
    workspace_id: str
    learning_goal_id: str
    learning_goal_revision: str
    capability_id: str
    kind: MasteryEvidenceKind
    result: MasteryEvidenceResult
    rubric_revision: str
    source_ref: str
    source_evidence_id: str
    session_id: str
    run_id: str
    summary: str
    metadata: Mapping[str, object]
    status: MasteryEvidenceStatus
    revision: int
    created_at: str
    invalidated_at: str | None = None
    invalidation_reason: str | None = None


@dataclass(frozen=True, slots=True)
class MasteryCapabilityProjection:
    capability_id: str
    label: str
    weight: float
    required: bool
    score: float
    status: Literal["unverified", "developing", "demonstrated"]
    evidence_count: int
    positive_kinds: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MasteryGoalProjection:
    workspace_id: str
    learning_goal_id: str
    learning_goal_revision: str
    rubric_revision: str
    score: float
    status: Literal["unverified", "in_progress", "demonstrated"]
    capabilities: tuple[MasteryCapabilityProjection, ...]
    evidence: tuple[MasteryEvidence, ...]


class MasteryLedger:
    """SQLite-backed evidence store with idempotent writes and deterministic reads."""

    def __init__(self, path: str | Path) -> None:
        expanded = Path(path).expanduser()
        self.path = expanded if expanded.is_absolute() else Path.cwd() / expanded
        self._initialized = False
        self._lock = Lock()
        self._validate_path()

    def record(self, evidence: MasteryEvidenceInput) -> MasteryEvidence:
        return self.record_many((evidence,))[0]

    def record_many(self, evidence: Iterable[MasteryEvidenceInput]) -> tuple[MasteryEvidence, ...]:
        normalized = tuple(_normalize_input(item) for item in evidence)
        if not normalized:
            return ()
        self._ensure_ready()
        stored: list[MasteryEvidence] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for item in normalized:
                    row = connection.execute(
                        "SELECT * FROM mastery_evidence WHERE owner_id = ? AND evidence_id = ?",
                        (item.owner_id, item.evidence_id),
                    ).fetchone()
                    if row is not None:
                        existing = _evidence_from_row(row)
                        if _immutable_fingerprint(existing) != _input_fingerprint(item):
                            raise MasteryLedgerConflictError(
                                "evidence_id already exists with different immutable content: "
                                f"{item.evidence_id}"
                            )
                        stored.append(existing)
                        continue
                    connection.execute(
                        """INSERT INTO mastery_evidence (
                            owner_id, evidence_id, workspace_id, learning_goal_id,
                            learning_goal_revision, capability_id, kind, result,
                            rubric_revision, source_ref, source_evidence_id, session_id,
                            run_id, summary, metadata_json, status, revision, created_at,
                            invalidated_at, invalidation_reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                  'valid', 1, ?, NULL, NULL)""",
                        _input_values(item),
                    )
                    inserted = connection.execute(
                        "SELECT * FROM mastery_evidence WHERE owner_id = ? AND evidence_id = ?",
                        (item.owner_id, item.evidence_id),
                    ).fetchone()
                    assert inserted is not None
                    stored.append(_evidence_from_row(inserted))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return tuple(stored)

    def invalidate(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        evidence_id: str,
        expected_revision: int,
        reason: str,
    ) -> MasteryEvidence:
        owner = _bounded(owner_id, "owner_id")
        workspace = _bounded(workspace_id, "workspace_id")
        evidence_key = _bounded(evidence_id, "evidence_id")
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expected_revision must be positive")
        normalized_reason = _bounded_text(reason, "reason", maximum=500)
        self._ensure_ready()
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM mastery_evidence WHERE owner_id = ? AND evidence_id = ? AND workspace_id = ?",
                (owner, evidence_key, workspace),
            ).fetchone()
            if row is None:
                raise MasteryLedgerNotFoundError(evidence_key)
            current = _evidence_from_row(row)
            if current.revision != expected_revision:
                raise MasteryLedgerConflictError(
                    f"evidence revision conflict: current revision is {current.revision}"
                )
            if current.status == "invalidated":
                return current
            connection.execute(
                """UPDATE mastery_evidence SET status='invalidated', revision=revision+1,
                   invalidated_at=?, invalidation_reason=?
                   WHERE owner_id=? AND evidence_id=? AND workspace_id=? AND revision=? AND status='valid'""",
                (now, normalized_reason, owner, evidence_key, workspace, expected_revision),
            )
            connection.commit()
            changed = connection.execute(
                "SELECT * FROM mastery_evidence WHERE owner_id = ? AND evidence_id = ?",
                (owner, evidence_key),
            ).fetchone()
        assert changed is not None
        return _evidence_from_row(changed)

    def list_evidence(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        learning_goal_id: str,
        learning_goal_revision: str,
        capability_id: str | None = None,
        include_invalidated: bool = True,
        limit: int = 200,
    ) -> tuple[MasteryEvidence, ...]:
        self._ensure_ready()
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        clauses = [
            "owner_id = ?",
            "workspace_id = ?",
            "learning_goal_id = ?",
            "learning_goal_revision = ?",
        ]
        params: list[object] = [
            _bounded(owner_id, "owner_id"),
            _bounded(workspace_id, "workspace_id"),
            _bounded(learning_goal_id, "learning_goal_id"),
            _bounded(learning_goal_revision, "learning_goal_revision"),
        ]
        if capability_id is not None:
            clauses.append("capability_id = ?")
            params.append(_bounded(capability_id, "capability_id"))
        if not include_invalidated:
            clauses.append("status = 'valid'")
        query = (
            "SELECT * FROM mastery_evidence WHERE "
            + " AND ".join(clauses)
            + " ORDER BY created_at DESC, evidence_id DESC LIMIT ?"
        )
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return tuple(_evidence_from_row(row) for row in rows)

    def project(
        self,
        *,
        owner_id: str,
        workspace_id: str,
        learning_goal_id: str,
        learning_goal_revision: str,
        capabilities: Iterable[MasteryCapability],
    ) -> MasteryGoalProjection:
        definitions = tuple(_normalize_capability(item) for item in capabilities)
        evidence = self.list_evidence(
            owner_id=owner_id,
            workspace_id=workspace_id,
            learning_goal_id=learning_goal_id,
            learning_goal_revision=learning_goal_revision,
            include_invalidated=True,
            limit=500,
        )
        valid_by_capability: dict[str, list[MasteryEvidence]] = {}
        for item in evidence:
            if item.status == "valid":
                valid_by_capability.setdefault(item.capability_id, []).append(item)
        projections: list[MasteryCapabilityProjection] = []
        for capability in definitions:
            items = valid_by_capability.get(capability.capability_id, [])
            latest_by_kind: dict[str, MasteryEvidence] = {}
            for item in items:
                current = latest_by_kind.get(item.kind)
                if current is None or (item.created_at, item.evidence_id) > (
                    current.created_at,
                    current.evidence_id,
                ):
                    latest_by_kind[item.kind] = item
            positive = tuple(
                sorted(
                    kind for kind, item in latest_by_kind.items() if _RESULT_SCORE[item.result] > 0
                )
            )
            score = min(
                1.0,
                sum(
                    _RELIABILITY[kind] * _RESULT_SCORE[item.result]
                    for kind, item in latest_by_kind.items()
                )
                / 2.0,
            )
            status: Literal["unverified", "developing", "demonstrated"]
            if not items:
                status = "unverified"
            elif len(positive) >= 2 and score >= 0.8:
                status = "demonstrated"
            else:
                status = "developing"
            projections.append(
                MasteryCapabilityProjection(
                    capability_id=capability.capability_id,
                    label=capability.label,
                    weight=capability.weight,
                    required=capability.required,
                    score=round(score, 4),
                    status=status,
                    evidence_count=len(items),
                    positive_kinds=positive,
                    evidence_ids=tuple(item.evidence_id for item in items),
                )
            )
        total_weight = sum(item.weight for item in definitions)
        score = (
            sum(item.score * item.weight for item in projections) / total_weight
            if total_weight
            else 0.0
        )
        required = [item for item in projections if item.required]
        goal_status: Literal["unverified", "in_progress", "demonstrated"]
        if not projections:
            goal_status = "unverified"
        elif (required or projections) and all(
            item.status == "demonstrated" for item in (required or projections)
        ):
            goal_status = "demonstrated"
        else:
            goal_status = (
                "in_progress" if any(item.status == "valid" for item in evidence) else "unverified"
            )
        return MasteryGoalProjection(
            workspace_id=_bounded(workspace_id, "workspace_id"),
            learning_goal_id=_bounded(learning_goal_id, "learning_goal_id"),
            learning_goal_revision=_bounded(learning_goal_revision, "learning_goal_revision"),
            rubric_revision=MASTERY_RUBRIC_REVISION,
            score=round(score, 4),
            status=goal_status,
            capabilities=tuple(projections),
            evidence=evidence,
        )

    def _validate_path(self) -> None:
        if self.path.is_symlink():
            raise ValueError("mastery database must not be a symlink")
        if self.path.parent.exists() and self.path.parent.is_symlink():
            raise ValueError("mastery database directory must not be a symlink")

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

    def _connect(self) -> sqlite3.Connection:
        self._validate_path()
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection


def _normalize_input(value: MasteryEvidenceInput) -> MasteryEvidenceInput:
    if not isinstance(value, MasteryEvidenceInput):
        raise TypeError("evidence must be MasteryEvidenceInput")
    kind = str(value.kind)
    result = str(value.result)
    if kind not in _KINDS or result not in _RESULTS:
        raise ValueError("unsupported mastery evidence kind or result")
    created = value.created_at or datetime.now(UTC).isoformat()
    _parse_timestamp(created)
    metadata = _bounded_metadata(value.metadata)
    return MasteryEvidenceInput(
        owner_id=_bounded(value.owner_id, "owner_id"),
        evidence_id=_bounded(value.evidence_id, "evidence_id"),
        workspace_id=_bounded(value.workspace_id, "workspace_id"),
        learning_goal_id=_bounded(value.learning_goal_id, "learning_goal_id"),
        learning_goal_revision=_bounded(value.learning_goal_revision, "learning_goal_revision"),
        capability_id=_bounded(value.capability_id, "capability_id"),
        kind=kind,  # type: ignore[arg-type]
        result=result,  # type: ignore[arg-type]
        source_ref=_bounded_ref(value.source_ref, "source_ref", maximum=1_000),
        source_evidence_id=_bounded(value.source_evidence_id, "source_evidence_id"),
        session_id=_bounded(value.session_id, "session_id"),
        run_id=_bounded(value.run_id, "run_id"),
        summary=_bounded_text(value.summary, "summary", maximum=_MAX_TEXT),
        metadata=metadata,
        created_at=created,
    )


def _normalize_capability(value: MasteryCapability) -> MasteryCapability:
    if not isinstance(value, MasteryCapability):
        raise TypeError("capabilities must be MasteryCapability")
    if not 0.1 <= float(value.weight) <= 10.0:
        raise ValueError("capability weight must be between 0.1 and 10")
    return MasteryCapability(
        capability_id=_bounded(value.capability_id, "capability_id"),
        label=_bounded_text(value.label, "capability label", maximum=160),
        weight=float(value.weight),
        required=bool(value.required),
    )


def _input_values(value: MasteryEvidenceInput) -> tuple[object, ...]:
    return (
        value.owner_id,
        value.evidence_id,
        value.workspace_id,
        value.learning_goal_id,
        value.learning_goal_revision,
        value.capability_id,
        value.kind,
        value.result,
        MASTERY_RUBRIC_REVISION,
        value.source_ref,
        value.source_evidence_id,
        value.session_id,
        value.run_id,
        value.summary,
        json.dumps(value.metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        value.created_at,
    )


def _input_fingerprint(value: MasteryEvidenceInput) -> tuple[object, ...]:
    return (
        value.owner_id,
        value.workspace_id,
        value.learning_goal_id,
        value.learning_goal_revision,
        value.capability_id,
        value.kind,
        value.result,
        MASTERY_RUBRIC_REVISION,
        value.source_ref,
        value.source_evidence_id,
        value.session_id,
        value.run_id,
        value.summary,
        json.dumps(value.metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _immutable_fingerprint(value: MasteryEvidence) -> tuple[object, ...]:
    return (
        value.owner_id,
        value.workspace_id,
        value.learning_goal_id,
        value.learning_goal_revision,
        value.capability_id,
        value.kind,
        value.result,
        value.rubric_revision,
        value.source_ref,
        value.source_evidence_id,
        value.session_id,
        value.run_id,
        value.summary,
        json.dumps(value.metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )


def _evidence_from_row(row: sqlite3.Row) -> MasteryEvidence:
    return MasteryEvidence(
        owner_id=str(row["owner_id"]),
        evidence_id=str(row["evidence_id"]),
        workspace_id=str(row["workspace_id"]),
        learning_goal_id=str(row["learning_goal_id"]),
        learning_goal_revision=str(row["learning_goal_revision"]),
        capability_id=str(row["capability_id"]),
        kind=str(row["kind"]),  # type: ignore[arg-type]
        result=str(row["result"]),  # type: ignore[arg-type]
        rubric_revision=str(row["rubric_revision"]),
        source_ref=str(row["source_ref"]),
        source_evidence_id=str(row["source_evidence_id"]),
        session_id=str(row["session_id"]),
        run_id=str(row["run_id"]),
        summary=str(row["summary"]),
        metadata=json.loads(str(row["metadata_json"])),
        status=str(row["status"]),  # type: ignore[arg-type]
        revision=int(row["revision"]),
        created_at=str(row["created_at"]),
        invalidated_at=(str(row["invalidated_at"]) if row["invalidated_at"] else None),
        invalidation_reason=(
            str(row["invalidation_reason"]) if row["invalidation_reason"] else None
        ),
    )


def _bounded(value: object, field: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{field} must be between 1 and {maximum} characters")
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} contains unsupported characters")
    return value


def _bounded_ref(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{field} must be between 1 and {maximum} characters")
    if any(char in value for char in "\x00\r\n"):
        raise ValueError(f"{field} contains unsupported control characters")
    return value


def _bounded_text(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field} must be non-empty and bounded")
    return normalized


def _bounded_metadata(value: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be a mapping")
    result: dict[str, object] = {}
    for raw_key, raw_value in list(value.items())[:_MAX_METADATA]:
        key = _bounded_text(str(raw_key), "metadata key", maximum=128)
        if isinstance(raw_value, str):
            result[key] = raw_value[:_MAX_TEXT]
        elif isinstance(raw_value, bool | int | float) or raw_value is None:
            result[key] = raw_value
    return result


def _parse_timestamp(value: str) -> None:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("created_at must be ISO-8601") from exc


__all__ = [
    "MASTERY_RUBRIC_REVISION",
    "MasteryCapability",
    "MasteryCapabilityProjection",
    "MasteryEvidence",
    "MasteryEvidenceInput",
    "MasteryEvidenceKind",
    "MasteryEvidenceResult",
    "MasteryEvidenceStatus",
    "MasteryGoalProjection",
    "MasteryLedger",
    "MasteryLedgerConflictError",
    "MasteryLedgerNotFoundError",
]
