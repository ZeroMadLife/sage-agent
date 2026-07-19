"""SQLite-reviewed, Git-projected Markdown knowledge workspace."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path, PurePosixPath
from threading import RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.knowledge.sources import ImmutableSourceArtifact

from core.knowledge.evolution import (
    EvidenceLearning,
    build_evidence_learning,
    deserialize_evidence_learning,
    serialize_evidence_learning,
)
from core.knowledge.goals import (
    LearningGoal,
    LearningGoalDefinition,
    default_learning_goal_content,
    parse_learning_goal,
    render_learning_goal,
)
from core.knowledge.graph import (
    KnowledgeGraphError,
    KnowledgeGraphNeighborhood,
    KnowledgeGraphNode,
    KnowledgeGraphOverview,
    KnowledgeGraphSnapshot,
    LocalKnowledgeGraph,
)
from core.knowledge.graph_analysis import (
    KnowledgeGraphAnalysis,
    KnowledgeGraphAnalysisError,
    LocalKnowledgeGraphAnalyzer,
)
from core.knowledge.index import LocalKnowledgeIndex
from core.knowledge.migration import (
    KnowledgeMigrationItem,
    KnowledgeMigrationPlan,
    KnowledgeMigrationResult,
    KnowledgeMigrationResultItem,
    build_migration_plan,
)
from core.knowledge.parsing import (
    DocumentParseError,
    DocumentRequiresOcrError,
    ParseArtifact,
    ParsedDocument,
    ParserConflictError,
    ParseRequest,
    ParserNotFoundError,
    ParserRegistry,
    default_parser_registry,
    deserialize_document,
    serialize_document,
)
from core.knowledge.policy import (
    POLICY_ID,
    POLICY_VERSION,
    KnowledgePolicyInput,
    evaluate_knowledge_policy,
    is_trusted_local_parser,
)
from core.knowledge.retrieval import (
    KnowledgeChunk,
    KnowledgeIndexSummary,
    KnowledgeRetrievalBundle,
    KnowledgeSearchHit,
    assemble_retrieval_bundle,
)
from core.knowledge.synthesis import (
    WorkspaceSynthesis,
    deserialize_synthesis,
    serialize_synthesis,
    source_evidence,
    synthesize_workspace,
)
from core.knowledge.understanding import (
    SourceUnderstanding,
    deserialize_understanding,
    serialize_understanding,
    understand_source,
)

_MAX_PROPOSAL_BYTES = 4 * 1024 * 1024
_ROOT_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_SCHEMA_VERSION = 9
_SOURCE_FORMATS = {
    ".md": ("text/markdown", 2 * 1024 * 1024),
    ".markdown": ("text/markdown", 2 * 1024 * 1024),
    ".html": ("text/html", 5 * 1024 * 1024),
    ".htm": ("text/html", 5 * 1024 * 1024),
    ".xhtml": ("application/xhtml+xml", 5 * 1024 * 1024),
    ".pdf": ("application/pdf", 20 * 1024 * 1024),
}
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?im)^[A-Z0-9_]*(?:API_KEY|ACCESS_TOKEN|CLIENT_SECRET)"
        r"\s*=\s*['\"]?[A-Za-z0-9_./+-]{20,}"
    ),
)
_INITIAL_OVERVIEW = "# Overview\n\n尚无已批准知识页面。\n"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_sources (
    source_id TEXT PRIMARY KEY,
    source_root_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    current_revision TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    title TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_root_id, relative_path)
);
CREATE TABLE IF NOT EXISTS knowledge_proposals (
    proposal_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_root_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_relative_path TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    page_id TEXT NOT NULL,
    target_path TEXT NOT NULL,
    title TEXT NOT NULL,
    proposed_content TEXT NOT NULL,
    base_page_revision TEXT NOT NULL,
    change_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    projection_status TEXT NOT NULL,
    revision INTEGER NOT NULL,
    parse_artifact_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_proposals_status_idx
    ON knowledge_proposals(status, created_at);
CREATE TABLE IF NOT EXISTS knowledge_parse_artifacts (
    artifact_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    parser_id TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    document_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_parse_artifacts_source_idx
    ON knowledge_parse_artifacts(source_id, source_revision);
CREATE TABLE IF NOT EXISTS knowledge_source_understandings (
    understanding_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    generator_id TEXT NOT NULL,
    generator_version TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_source_understandings_source_idx
    ON knowledge_source_understandings(source_id, source_revision);
CREATE TABLE IF NOT EXISTS knowledge_workspace_syntheses (
    synthesis_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE,
    input_hash TEXT NOT NULL,
    generator_id TEXT NOT NULL,
    generator_version TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_workspace_syntheses_input_idx
    ON knowledge_workspace_syntheses(input_hash, created_at);
CREATE TABLE IF NOT EXISTS knowledge_evidence_learnings (
    learning_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE,
    input_hash TEXT NOT NULL,
    generator_id TEXT NOT NULL,
    generator_version TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_evidence_learnings_input_idx
    ON knowledge_evidence_learnings(input_hash, created_at);
CREATE TABLE IF NOT EXISTS knowledge_policy_decisions (
    decision_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE,
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    action TEXT NOT NULL,
    reason_codes_json TEXT NOT NULL,
    base_page_revision TEXT NOT NULL,
    applied_page_revision TEXT,
    undo_proposal_id TEXT,
    undo_page_revision TEXT,
    undone_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_policy_decisions_action_idx
    ON knowledge_policy_decisions(action, created_at);
CREATE TABLE IF NOT EXISTS knowledge_events (
    event_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    revision INTEGER NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS knowledge_events_proposal_idx
    ON knowledge_events(proposal_id, created_at);
CREATE TABLE IF NOT EXISTS knowledge_pages (
    page_id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    current_revision TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_page_revisions (
    revision_id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    content TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    change_kind TEXT NOT NULL,
    git_commit TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(page_id, sequence)
);
CREATE INDEX IF NOT EXISTS knowledge_page_revisions_page_idx
    ON knowledge_page_revisions(page_id, sequence);
"""


class KnowledgeStoreError(RuntimeError):
    """Base knowledge persistence error."""


class KnowledgeConflictError(KnowledgeStoreError):
    """A proposal or page revision no longer matches its expected base."""


class KnowledgeProjectionError(KnowledgeStoreError):
    """An approved proposal could not be projected into the Git wiki."""


class KnowledgeEvidenceError(KnowledgeStoreError):
    """A learning deposit references missing, stale, or invalid evidence."""


@dataclass(frozen=True, slots=True)
class KnowledgeSourceRoot:
    root_id: str
    kind: str
    label: str
    path: Path


@dataclass(frozen=True, slots=True)
class KnowledgeProposal:
    proposal_id: str
    source_id: str
    source_root_id: str
    source_kind: str
    source_relative_path: str
    source_revision: str
    raw_path: str
    page_id: str
    target_path: str
    title: str
    proposed_content: str
    base_page_revision: str
    change_kind: str
    status: str
    projection_status: str
    revision: int
    parse_artifact_id: str | None
    error: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class KnowledgeEvent:
    event_id: str
    proposal_id: str
    event_type: str
    revision: int
    detail: dict[str, str]
    created_at: str


@dataclass(frozen=True, slots=True)
class KnowledgePolicyDecision:
    decision_id: str
    proposal_id: str
    policy_id: str
    policy_version: str
    risk_level: str
    action: str
    reason_codes: tuple[str, ...]
    base_page_revision: str
    applied_page_revision: str | None
    undo_proposal_id: str | None
    undo_page_revision: str | None
    undone_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class KnowledgePageRevision:
    revision_id: str
    sequence: int
    content_hash: str
    source_revision: str
    proposal_id: str
    change_kind: str
    git_commit: str
    created_at: str


@dataclass(frozen=True, slots=True)
class KnowledgePage:
    page_id: str
    path: str
    title: str
    current_revision: str
    updated_at: str
    revisions: tuple[KnowledgePageRevision, ...]


@dataclass(frozen=True, slots=True)
class KnowledgePageDocument:
    page_id: str
    path: str
    title: str
    updated_at: str
    revision: KnowledgePageRevision
    content: str


@dataclass(frozen=True, slots=True)
class KnowledgeSummary:
    status: str
    workspace_name: str
    source_count: int
    wiki_page_count: int
    pending_proposal_count: int
    last_synced_at: str | None
    source_roots: tuple[KnowledgeSourceRoot, ...]


@dataclass(frozen=True, slots=True)
class PreparedKnowledgeSource:
    """Validated immutable parser input ready for an auditable proposal."""

    source_root_id: str
    source_kind: str
    relative_path: str
    source_id: str
    source_revision: str
    payload: bytes
    document: ParsedDocument
    understanding: SourceUnderstanding | None = None


@dataclass(frozen=True, slots=True)
class LoadedKnowledgeSource:
    """Validated source bytes ready for local or policy-gated external parsing."""

    source_root_id: str
    source_kind: str
    relative_path: str
    source_id: str
    source_revision: str
    media_type: str
    payload: bytes

    def parse_request(self) -> ParseRequest:
        return ParseRequest(
            source_id=self.source_id,
            relative_path=self.relative_path,
            source_revision=self.source_revision,
            media_type=self.media_type,
            payload=self.payload,
        )


class KnowledgeStore:
    """Own the source trust boundary and all auditable Wiki transitions."""

    def __init__(
        self,
        workspace_root: str | Path,
        database_path: str | Path,
        source_roots: Mapping[str, KnowledgeSourceRoot],
        parser_registry: ParserRegistry | None = None,
        knowledge_index: LocalKnowledgeIndex | None = None,
        knowledge_graph: LocalKnowledgeGraph | None = None,
        knowledge_graph_analyzer: LocalKnowledgeGraphAnalyzer | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.database_path = Path(database_path).expanduser().resolve()
        self.source_roots = self._validate_source_roots(source_roots)
        self.parser_registry = parser_registry or default_parser_registry()
        self.knowledge_index = knowledge_index or LocalKnowledgeIndex()
        self.knowledge_graph = knowledge_graph or LocalKnowledgeGraph(
            workspace_id=self.knowledge_index.workspace_id
        )
        self.knowledge_graph_analyzer = knowledge_graph_analyzer or LocalKnowledgeGraphAnalyzer(
            workspace_id=self.knowledge_index.workspace_id
        )
        self._lock = RLock()
        self._initialized = False

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            self._prepare_workspace()
            self._prepare_database()
            self._initialized = True

    def summary(self) -> KnowledgeSummary:
        self.initialize()
        with self._connect() as connection:
            source_count = int(
                connection.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0]
            )
            page_count = int(
                connection.execute("SELECT COUNT(*) FROM knowledge_pages").fetchone()[0]
            )
            pending = int(
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_proposals WHERE status='pending'"
                ).fetchone()[0]
            )
            last = connection.execute("SELECT MAX(updated_at) FROM knowledge_pages").fetchone()[0]
        return KnowledgeSummary(
            status="ready",
            workspace_name=self.workspace_root.name or "knowledge",
            source_count=source_count,
            wiki_page_count=page_count,
            pending_proposal_count=pending,
            last_synced_at=str(last) if last else None,
            source_roots=tuple(self.source_roots.values()),
        )

    def register_source_root(self, source: KnowledgeSourceRoot) -> KnowledgeSourceRoot:
        """Register one server-owned connector root without rebuilding the store."""

        validated = self._validate_source_roots({source.root_id: source})[source.root_id]
        with self._lock:
            existing = self.source_roots.get(validated.root_id)
            if existing is not None and existing != validated:
                raise KnowledgeConflictError("knowledge source root already exists")
            self.source_roots[validated.root_id] = validated
        return validated

    def ingest(self, source_root_id: str, relative_path: str) -> KnowledgeProposal:
        return self.ingest_prepared(self.prepare_ingest(source_root_id, relative_path))

    def propose_source_retraction(
        self,
        source_root_id: str,
        relative_path: str,
        expected_source_revision: str,
    ) -> KnowledgeProposal:
        """Create one reviewable tombstone when a previously indexed source disappears."""

        self.initialize()
        normalized = _relative_source_path(relative_path)
        if not expected_source_revision.startswith("sha256:"):
            raise KnowledgeConflictError("deleted source revision is invalid")
        source_id = (
            "src_"
            + hashlib.sha256(f"{source_root_id}\0{normalized.as_posix()}".encode()).hexdigest()[:32]
        )
        with self._connect() as connection:
            source = connection.execute(
                "SELECT * FROM knowledge_sources WHERE source_id=? AND source_root_id=?",
                (source_id, source_root_id),
            ).fetchone()
            if source is None:
                raise KeyError(relative_path)
            if str(source["current_revision"]) != expected_source_revision:
                raise KnowledgeConflictError("source revision changed; create a new batch")
            original_row = connection.execute(
                """
                SELECT * FROM knowledge_proposals
                WHERE source_id=? AND source_root_id=? AND source_relative_path=?
                  AND source_revision=? AND change_kind='ingest'
                  AND status='approved' AND projection_status='complete'
                ORDER BY updated_at DESC, proposal_id DESC LIMIT 1
                """,
                (source_id, source_root_id, normalized.as_posix(), expected_source_revision),
            ).fetchone()
            if original_row is None:
                raise KeyError(relative_path)
            existing = connection.execute(
                """
                SELECT * FROM knowledge_proposals
                WHERE source_id=? AND change_kind='retraction'
                  AND status IN ('pending', 'approved')
                ORDER BY updated_at DESC, proposal_id DESC LIMIT 1
                """,
                (f"retraction:{original_row['proposal_id']}",),
            ).fetchone()
            if existing is not None:
                return _proposal(existing)
            original = _proposal(original_row)
            page = connection.execute(
                "SELECT current_revision FROM knowledge_pages WHERE page_id=?",
                (original.page_id,),
            ).fetchone()
            if page is None:
                raise KeyError(relative_path)
            expected_page_revision = str(page["current_revision"])
        return self._propose_retraction(original, expected_page_revision)

    def prepare_ingest(self, source_root_id: str, relative_path: str) -> PreparedKnowledgeSource:
        source = self.load_source(source_root_id, relative_path)
        document = self.parser_registry.parse(source.parse_request())
        return self.prepare_parsed_source(source, document)

    def load_source(self, source_root_id: str, relative_path: str) -> LoadedKnowledgeSource:
        self.initialize()
        root = self.source_roots.get(source_root_id)
        if root is None:
            raise KeyError(source_root_id)
        normalized = _relative_source_path(relative_path)
        media_type, max_bytes = _source_format(normalized)
        source_path = root.path / normalized
        payload = _read_source_bytes(root.path, source_path, max_bytes=max_bytes)
        _scan_source_secrets(payload, media_type)
        digest = hashlib.sha256(payload).hexdigest()
        source_revision = f"sha256:{digest}"
        source_id = (
            "src_"
            + hashlib.sha256(f"{source_root_id}\0{normalized.as_posix()}".encode()).hexdigest()[:32]
        )
        return LoadedKnowledgeSource(
            source_root_id=source_root_id,
            source_kind=root.kind,
            relative_path=normalized.as_posix(),
            source_id=source_id,
            source_revision=source_revision,
            media_type=media_type,
            payload=payload,
        )

    def load_artifact(
        self,
        source_root_id: str,
        artifact: ImmutableSourceArtifact,
    ) -> LoadedKnowledgeSource:
        """Validate provider bytes and convert them into the canonical parser input."""

        self.initialize()
        root = self.source_roots.get(source_root_id)
        if root is None:
            raise KeyError(source_root_id)
        normalized = _relative_source_path(artifact.source_key)
        media_type, max_bytes = _source_format(normalized)
        if artifact.media_type != media_type or len(artifact.content) > max_bytes:
            raise KnowledgeConflictError("source adapter returned an invalid artifact")
        digest = hashlib.sha256(artifact.content).hexdigest()
        revision = f"sha256:{digest}"
        if revision != artifact.source_revision:
            raise KnowledgeConflictError("source adapter returned a stale artifact")
        _scan_source_secrets(artifact.content, media_type)
        source_id = (
            "src_"
            + hashlib.sha256(
                f"{source_root_id}\0{normalized.as_posix()}".encode()
            ).hexdigest()[:32]
        )
        return LoadedKnowledgeSource(
            source_root_id=source_root_id,
            source_kind=root.kind,
            relative_path=normalized.as_posix(),
            source_id=source_id,
            source_revision=revision,
            media_type=media_type,
            payload=artifact.content,
        )

    def prepare_parsed_source(
        self,
        source: LoadedKnowledgeSource,
        document: ParsedDocument,
    ) -> PreparedKnowledgeSource:
        request = source.parse_request()
        _validate_parsed_document(request, document)
        _scan_text_secrets(f"{document.title}\n{document.rendered_markdown}")
        artifact_id = _parse_artifact_id(source.source_id, source.source_revision, document)
        return PreparedKnowledgeSource(
            source_root_id=source.source_root_id,
            source_kind=source.source_kind,
            relative_path=source.relative_path,
            source_id=source.source_id,
            source_revision=source.source_revision,
            payload=source.payload,
            document=document,
            understanding=understand_source(artifact_id, document),
        )

    def ingest_prepared(self, prepared: PreparedKnowledgeSource) -> KnowledgeProposal:
        self.initialize()
        root = self.source_roots.get(prepared.source_root_id)
        if root is None or root.kind != prepared.source_kind:
            raise KnowledgeConflictError("prepared knowledge source is stale")
        normalized = _relative_source_path(prepared.relative_path)
        media_type, max_bytes = _source_format(normalized)
        current_payload = _read_source_bytes(root.path, root.path / normalized, max_bytes=max_bytes)
        current_revision = "sha256:" + hashlib.sha256(current_payload).hexdigest()
        expected_source_id = (
            "src_"
            + hashlib.sha256(
                f"{prepared.source_root_id}\0{normalized.as_posix()}".encode()
            ).hexdigest()[:32]
        )
        if (
            current_revision != prepared.source_revision
            or current_payload != prepared.payload
            or prepared.source_id != expected_source_id
        ):
            raise KnowledgeConflictError("source revision changed during parsing")
        _scan_source_secrets(current_payload, media_type)
        document = prepared.document
        request = ParseRequest(
            source_id=prepared.source_id,
            relative_path=prepared.relative_path,
            source_revision=prepared.source_revision,
            media_type=media_type,
            payload=prepared.payload,
        )
        _validate_parsed_document(request, document)
        _scan_text_secrets(f"{document.title}\n{document.rendered_markdown}")
        source_id = prepared.source_id
        source_revision = prepared.source_revision
        title = document.title
        digest = source_revision.removeprefix("sha256:")
        slug = _slug(normalized.with_suffix("").as_posix())
        page_id = "page_" + source_id.removeprefix("src_")
        target_path = f"wiki/sources/{slug}-{source_id[-8:]}.md"
        raw_path = f"raw/sources/{root.kind}/{digest[:2]}/{digest}{normalized.suffix.lower()}"
        _write_immutable_bytes(self.workspace_root / raw_path, prepared.payload)
        payload_json = serialize_document(document)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        artifact_id = _parse_artifact_id(source_id, source_revision, document)
        understanding = prepared.understanding or understand_source(artifact_id, document)
        if understanding.artifact_id != artifact_id:
            raise KnowledgeConflictError("source understanding references a stale parse artifact")
        understanding_json = serialize_understanding(understanding)
        understanding_hash = hashlib.sha256(understanding_json.encode("utf-8")).hexdigest()

        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            page = connection.execute(
                "SELECT current_revision FROM knowledge_pages WHERE page_id=?",
                (page_id,),
            ).fetchone()
            base_revision = str(page["current_revision"]) if page else ""
            proposed = _source_page(
                page_id=page_id,
                title=title,
                source_id=source_id,
                source_kind=root.kind,
                source_relative_path=normalized.as_posix(),
                source_revision=source_revision,
                raw_path=raw_path,
                content=document.rendered_markdown,
                parser_id=document.provenance.parser_id,
                parser_version=document.provenance.parser_version,
                parsed_document_id=document.document_id,
            )
            if page is not None:
                revision = connection.execute(
                    """
                    SELECT content_hash FROM knowledge_page_revisions
                    WHERE revision_id=?
                    """,
                    (base_revision,),
                ).fetchone()
                if revision is not None and str(revision["content_hash"]) == _content_hash(
                    proposed
                ):
                    existing = connection.execute(
                        """
                        SELECT * FROM knowledge_proposals
                        WHERE source_id=? AND source_revision=?
                          AND parse_artifact_id=?
                          AND status='approved' AND projection_status='complete'
                        ORDER BY updated_at DESC LIMIT 1
                        """,
                        (source_id, source_revision, artifact_id),
                    ).fetchone()
                    if existing is not None:
                        connection.rollback()
                        return _proposal(existing)
            proposal_id = (
                "kprop_"
                + hashlib.sha256(
                    f"{source_id}\0{source_revision}\0{base_revision}\0{artifact_id}".encode()
                ).hexdigest()[:32]
            )
            existing = connection.execute(
                "SELECT * FROM knowledge_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                return _proposal(existing)
            now = _now()
            connection.execute(
                """
                INSERT INTO knowledge_sources (
                    source_id, source_root_id, source_kind, relative_path,
                    current_revision, raw_path, title, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    current_revision=excluded.current_revision,
                    raw_path=excluded.raw_path,
                    title=excluded.title,
                    updated_at=excluded.updated_at
                """,
                (
                    source_id,
                    prepared.source_root_id,
                    root.kind,
                    normalized.as_posix(),
                    source_revision,
                    raw_path,
                    title,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO knowledge_proposals (
                    proposal_id, source_id, source_root_id, source_kind,
                    source_relative_path, source_revision, raw_path, page_id,
                    target_path, title, proposed_content, base_page_revision,
                    change_kind, status, projection_status, revision,
                    parse_artifact_id, error,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ingest',
                          'pending', 'pending', 0, ?, NULL, ?, ?)
                """,
                (
                    proposal_id,
                    source_id,
                    prepared.source_root_id,
                    root.kind,
                    normalized.as_posix(),
                    source_revision,
                    raw_path,
                    page_id,
                    target_path,
                    title,
                    proposed,
                    base_revision,
                    artifact_id,
                    now,
                    now,
                ),
            )
            existing_artifact = connection.execute(
                "SELECT payload_hash, payload_json FROM knowledge_parse_artifacts "
                "WHERE artifact_id=?",
                (artifact_id,),
            ).fetchone()
            if existing_artifact is not None and (
                str(existing_artifact["payload_hash"]) != payload_hash
                or str(existing_artifact["payload_json"]) != payload_json
            ):
                connection.rollback()
                raise KnowledgeConflictError("immutable parse artifact conflict")
            connection.execute(
                """
                INSERT OR IGNORE INTO knowledge_parse_artifacts (
                    artifact_id, source_id, source_revision, parser_id,
                    parser_version, document_id, payload_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id,
                    source_id,
                    source_revision,
                    document.provenance.parser_id,
                    document.provenance.parser_version,
                    document.document_id,
                    payload_hash,
                    payload_json,
                    now,
                ),
            )
            existing_understanding = connection.execute(
                "SELECT payload_hash, payload_json FROM knowledge_source_understandings "
                "WHERE understanding_id=?",
                (understanding.understanding_id,),
            ).fetchone()
            if existing_understanding is not None and (
                str(existing_understanding["payload_hash"]) != understanding_hash
                or str(existing_understanding["payload_json"]) != understanding_json
            ):
                connection.rollback()
                raise KnowledgeConflictError("immutable source understanding conflict")
            connection.execute(
                """
                INSERT OR IGNORE INTO knowledge_source_understandings (
                    understanding_id, artifact_id, source_id, source_revision,
                    generator_id, generator_version, payload_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    understanding.understanding_id,
                    artifact_id,
                    source_id,
                    source_revision,
                    understanding.generator_id,
                    understanding.generator_version,
                    understanding_hash,
                    understanding_json,
                    now,
                ),
            )
            self._event(
                connection,
                proposal_id,
                "proposal_created",
                0,
                {
                    "parse_artifact_id": artifact_id,
                    "parser_id": document.provenance.parser_id,
                    "parser_version": document.provenance.parser_version,
                    "understanding_id": understanding.understanding_id,
                    "understanding_generator": understanding.generator_id,
                },
            )
            connection.commit()
        return self.get_proposal(proposal_id)

    def get_proposal(self, proposal_id: str) -> KnowledgeProposal:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_proposals WHERE proposal_id=?",
                (_bounded_id(proposal_id),),
            ).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return _proposal(row)

    def get_parse_artifact(self, proposal_id: str) -> ParseArtifact | None:
        """Load and integrity-check the immutable parse result for a proposal."""

        proposal = self.get_proposal(proposal_id)
        if proposal.parse_artifact_id is None:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_parse_artifacts WHERE artifact_id=?",
                (proposal.parse_artifact_id,),
            ).fetchone()
        if row is None:
            raise KnowledgeStoreError("parse artifact is missing")
        payload_json = str(row["payload_json"])
        if hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != str(row["payload_hash"]):
            raise KnowledgeStoreError("parse artifact integrity check failed")
        try:
            document = deserialize_document(payload_json)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise KnowledgeStoreError("parse artifact integrity check failed") from exc
        if (
            document.document_id != str(row["document_id"])
            or document.source_id != str(row["source_id"])
            or document.source_revision != str(row["source_revision"])
            or document.provenance.input_revision != str(row["source_revision"])
            or document.provenance.parser_id != str(row["parser_id"])
            or document.provenance.parser_version != str(row["parser_version"])
            or document.source_id != proposal.source_id
            or document.source_revision != proposal.source_revision
        ):
            raise KnowledgeStoreError("parse artifact integrity check failed")
        return ParseArtifact(
            artifact_id=str(row["artifact_id"]),
            proposal_id=proposal.proposal_id,
            document=document,
            created_at=str(row["created_at"]),
        )

    def get_source_understanding(self, proposal_id: str) -> SourceUnderstanding | None:
        """Load and integrity-check the immutable understanding for a proposal."""
        artifact = self.get_parse_artifact(proposal_id)
        if artifact is None:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_source_understandings WHERE artifact_id=?",
                (artifact.artifact_id,),
            ).fetchone()
        if row is None:
            raise KnowledgeStoreError("source understanding is missing")
        payload_json = str(row["payload_json"])
        if hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != str(row["payload_hash"]):
            raise KnowledgeStoreError("source understanding integrity check failed")
        try:
            understanding = deserialize_understanding(payload_json)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise KnowledgeStoreError("source understanding integrity check failed") from exc
        if (
            understanding.understanding_id != str(row["understanding_id"])
            or understanding.artifact_id != artifact.artifact_id
            or understanding.source_id != artifact.document.source_id
            or understanding.source_revision != artifact.document.source_revision
            or understanding.generator_id != str(row["generator_id"])
            or understanding.generator_version != str(row["generator_version"])
        ):
            raise KnowledgeStoreError("source understanding integrity check failed")
        return understanding

    def propose_workspace_synthesis(self) -> KnowledgeProposal:
        """Create an idempotent overview proposal from current approved source pages."""
        self.initialize()
        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT p.page_id, p.path, p.current_revision,
                       r.proposal_id, u.payload_json AS understanding_json
                FROM knowledge_pages AS p
                JOIN knowledge_page_revisions AS r
                  ON r.revision_id = p.current_revision
                JOIN knowledge_proposals AS proposal
                  ON proposal.proposal_id = r.proposal_id
                JOIN knowledge_source_understandings AS u
                  ON u.artifact_id = proposal.parse_artifact_id
                WHERE proposal.change_kind = 'ingest'
                  AND proposal.status = 'approved'
                  AND proposal.projection_status = 'complete'
                ORDER BY p.title, p.page_id
                """
            ).fetchall()
            evidence = []
            for row in rows:
                try:
                    understanding = deserialize_understanding(str(row["understanding_json"]))
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    connection.rollback()
                    raise KnowledgeStoreError(
                        "source understanding integrity check failed"
                    ) from exc
                evidence.append(
                    source_evidence(
                        page_id=str(row["page_id"]),
                        page_revision=str(row["current_revision"]),
                        proposal_id=str(row["proposal_id"]),
                        path=str(row["path"]),
                        understanding=understanding,
                    )
                )
            synthesis = synthesize_workspace(tuple(evidence))
            overview = connection.execute(
                "SELECT current_revision FROM knowledge_pages WHERE page_id=?",
                ("page_workspace_overview",),
            ).fetchone()
            base_revision = str(overview["current_revision"]) if overview else ""
            proposal_id = "kprop_" + hashlib.sha256(
                f"{synthesis.synthesis_id}\0{base_revision}".encode()
            ).hexdigest()[:32]
            existing = connection.execute(
                "SELECT * FROM knowledge_proposals WHERE proposal_id=?",
                (proposal_id,),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                return _proposal(existing)
            now = _now()
            connection.execute(
                """
                INSERT INTO knowledge_proposals (
                    proposal_id, source_id, source_root_id, source_kind,
                    source_relative_path, source_revision, raw_path, page_id,
                    target_path, title, proposed_content, base_page_revision,
                    change_kind, status, projection_status, revision,
                    parse_artifact_id, error, created_at, updated_at
                ) VALUES (?, ?, 'knowledge', 'synthesis', 'overview.md', ?, '', ?,
                          'overview.md', 'Knowledge Overview', ?, ?, 'synthesis',
                          'pending', 'pending', 0, NULL, NULL, ?, ?)
                """,
                (
                    proposal_id,
                    f"synthesis:{synthesis.synthesis_id}",
                    synthesis.input_hash,
                    "page_workspace_overview",
                    synthesis.rendered_markdown,
                    base_revision,
                    now,
                    now,
                ),
            )
            payload_json = serialize_synthesis(synthesis)
            connection.execute(
                """
                INSERT INTO knowledge_workspace_syntheses (
                    synthesis_id, proposal_id, input_hash, generator_id,
                    generator_version, payload_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    synthesis.synthesis_id,
                    proposal_id,
                    synthesis.input_hash,
                    synthesis.generator_id,
                    synthesis.generator_version,
                    hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                    payload_json,
                    now,
                ),
            )
            self._event(
                connection,
                proposal_id,
                "synthesis_proposed",
                0,
                {
                    "synthesis_id": synthesis.synthesis_id,
                    "input_hash": synthesis.input_hash,
                    "source_count": str(len(synthesis.sources)),
                },
            )
            connection.commit()
        return self.get_proposal(proposal_id)

    def get_workspace_synthesis(self, proposal_id: str) -> WorkspaceSynthesis | None:
        self.get_proposal(proposal_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_workspace_syntheses WHERE proposal_id=?",
                (_bounded_id(proposal_id),),
            ).fetchone()
        if row is None:
            return None
        payload_json = str(row["payload_json"])
        if hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != str(row["payload_hash"]):
            raise KnowledgeStoreError("workspace synthesis integrity check failed")
        try:
            synthesis = deserialize_synthesis(payload_json)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise KnowledgeStoreError("workspace synthesis integrity check failed") from exc
        if (
            synthesis.synthesis_id != str(row["synthesis_id"])
            or synthesis.input_hash != str(row["input_hash"])
            or synthesis.generator_id != str(row["generator_id"])
            or synthesis.generator_version != str(row["generator_version"])
        ):
            raise KnowledgeStoreError("workspace synthesis integrity check failed")
        return synthesis

    def propose_evidence_learning(
        self,
        topic: str,
        citation_ids: tuple[str, ...],
        *,
        session_id: str = "",
        run_id: str = "",
        event_id: str = "",
    ) -> KnowledgeProposal:
        """Auto-apply an extractive learning page backed by current citations."""

        self.initialize()
        normalized_topic = " ".join(topic.split())
        if not normalized_topic or len(normalized_topic) > 160:
            raise ValueError("knowledge learning topic must be between 1 and 160 characters")
        _scan_text_secrets(normalized_topic)
        for value in (session_id, run_id, event_id):
            if len(value) > 128:
                raise ValueError("knowledge learning provenance identifier is too long")
        topic_digest = hashlib.sha256(normalized_topic.lower().encode("utf-8")).hexdigest()
        page_id = f"page_learning_{topic_digest[:32]}"
        target_path = f"wiki/learnings/{_slug(normalized_topic)}-{topic_digest[-8:]}.md"
        source_id = f"learning_{topic_digest[:32]}"
        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                resolved = self.knowledge_index.resolve_citations(
                    connection, citation_ids, visibility="private"
                )
            except (KeyError, ValueError) as exc:
                connection.rollback()
                raise KnowledgeEvidenceError(str(exc)) from exc
            if any(
                chunk.source_kind
                not in {"obsidian", "markdown", "github", "feishu", "web"}
                for _citation_id, chunk in resolved
            ):
                connection.rollback()
                raise KnowledgeEvidenceError(
                    "knowledge learning citations must reference original source material"
                )
            page = connection.execute(
                """
                SELECT page.current_revision, revision.content
                FROM knowledge_pages AS page
                JOIN knowledge_page_revisions AS revision
                  ON revision.revision_id=page.current_revision
                WHERE page.page_id=?
                """,
                (page_id,),
            ).fetchone()
            base_revision = str(page["current_revision"]) if page else ""
            base_content = str(page["content"]) if page else ""
            learning = build_evidence_learning(
                topic=normalized_topic,
                page_id=page_id,
                target_path=target_path,
                resolved_citations=resolved,
                base_content=base_content,
                session_id=session_id,
                run_id=run_id,
                event_id=event_id,
            )
            _scan_text_secrets(learning.rendered_markdown)
            existing = connection.execute(
                """
                SELECT proposal.*
                FROM knowledge_evidence_learnings AS learning
                JOIN knowledge_proposals AS proposal
                  ON proposal.proposal_id=learning.proposal_id
                WHERE learning.learning_id=?
                """,
                (learning.learning_id,),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                return _proposal(existing)
            proposal_id = "kprop_" + hashlib.sha256(
                f"{learning.learning_id}\0{base_revision}".encode()
            ).hexdigest()[:32]
            now = _now()
            connection.execute(
                """
                INSERT INTO knowledge_proposals (
                    proposal_id, source_id, source_root_id, source_kind,
                    source_relative_path, source_revision, raw_path, page_id,
                    target_path, title, proposed_content, base_page_revision,
                    change_kind, status, projection_status, revision,
                    parse_artifact_id, error, created_at, updated_at
                ) VALUES (?, ?, 'knowledge', 'agent_learning', ?, ?, '', ?, ?, ?, ?, ?,
                          'learning', 'pending', 'pending', 0, NULL, NULL, ?, ?)
                """,
                (
                    proposal_id,
                    source_id,
                    target_path,
                    learning.input_hash,
                    page_id,
                    target_path,
                    normalized_topic,
                    learning.rendered_markdown,
                    base_revision,
                    now,
                    now,
                ),
            )
            payload_json = serialize_evidence_learning(learning)
            connection.execute(
                """
                INSERT INTO knowledge_evidence_learnings (
                    learning_id, proposal_id, input_hash, generator_id,
                    generator_version, payload_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    learning.learning_id,
                    proposal_id,
                    learning.input_hash,
                    learning.generator_id,
                    learning.generator_version,
                    hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                    payload_json,
                    now,
                ),
            )
            self._event(
                connection,
                proposal_id,
                "learning_evidence_verified",
                0,
                {
                    "learning_id": learning.learning_id,
                    "input_hash": learning.input_hash,
                    "citation_count": str(len(learning.citations)),
                    "session_id": session_id,
                    "run_id": run_id,
                    "event_id": event_id,
                },
            )
            connection.commit()
        return self.evaluate_and_apply_policy(proposal_id)

    def get_evidence_learning(self, proposal_id: str) -> EvidenceLearning | None:
        proposal = self.get_proposal(proposal_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_evidence_learnings WHERE proposal_id=?",
                (_bounded_id(proposal_id),),
            ).fetchone()
        if row is None:
            return None
        payload_json = str(row["payload_json"])
        if hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != str(
            row["payload_hash"]
        ):
            raise KnowledgeStoreError("evidence learning integrity check failed")
        try:
            learning = deserialize_evidence_learning(payload_json)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise KnowledgeStoreError("evidence learning integrity check failed") from exc
        if (
            learning.learning_id != str(row["learning_id"])
            or learning.input_hash != str(row["input_hash"])
            or learning.generator_id != str(row["generator_id"])
            or learning.generator_version != str(row["generator_version"])
            or learning.page_id != proposal.page_id
            or learning.target_path != proposal.target_path
            or learning.rendered_markdown != proposal.proposed_content
        ):
            raise KnowledgeStoreError("evidence learning integrity check failed")
        return learning

    def _evidence_learning_is_current(self, learning: EvidenceLearning) -> bool:
        citation_ids = tuple(item.citation_id for item in learning.citations)
        try:
            with self._connect() as connection:
                resolved = self.knowledge_index.resolve_citations(
                    connection, citation_ids, visibility="private"
                )
        except (KeyError, ValueError):
            return False
        resolved_by_id = {citation: chunk for citation, chunk in resolved}
        return all(
            resolved_by_id[item.citation_id].chunk_id == item.chunk_id
            and resolved_by_id[item.citation_id].page_revision == item.page_revision
            and resolved_by_id[item.citation_id].source_revision == item.source_revision
            for item in learning.citations
        )

    def list_proposals(self, status: str | None = None) -> list[KnowledgeProposal]:
        self.initialize()
        if status not in {None, "pending", "approved", "rejected"}:
            raise ValueError("invalid proposal status")
        with self._connect() as connection:
            if status is None:
                rows = connection.execute(
                    "SELECT * FROM knowledge_proposals ORDER BY created_at DESC LIMIT 200"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM knowledge_proposals WHERE status=? "
                    "ORDER BY created_at DESC LIMIT 200",
                    (status,),
                ).fetchall()
        return [_proposal(row) for row in rows]

    def plan_pending_migration(self, *, limit: int = 500) -> KnowledgeMigrationPlan:
        """Reassess legacy pending proposals without mutating proposal or Git state."""

        self.initialize()
        if limit < 1 or limit > 1000:
            raise ValueError("knowledge migration limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_proposals
                WHERE status='pending'
                ORDER BY created_at, proposal_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        items = [self._plan_pending_proposal(_proposal(row)) for row in rows]
        return build_migration_plan(items)

    def execute_pending_migration(
        self, expected_plan_id: str, *, limit: int = 500
    ) -> KnowledgeMigrationResult:
        """Apply a fresh migration plan item by item so failures remain retryable."""

        plan = self.plan_pending_migration(limit=limit)
        if plan.plan_id != _bounded_id(expected_plan_id):
            raise KnowledgeConflictError("knowledge migration plan changed")
        results: list[KnowledgeMigrationResultItem] = []
        for item in plan.items:
            try:
                if item.disposition == "auto_apply":
                    replacement = self.ingest(
                        item.source_root_id,
                        item.source_relative_path,
                    )
                    replacement = self.evaluate_and_apply_policy(replacement.proposal_id)
                    if (
                        replacement.status != "approved"
                        or replacement.projection_status != "complete"
                    ):
                        raise KnowledgeConflictError(
                            "reassessed proposal did not satisfy auto-apply policy"
                        )
                    if replacement.proposal_id != item.proposal_id:
                        self._retire_pending_proposal(
                            item.proposal_id,
                            reason_code="superseded_by_reparse",
                            replacement_proposal_id=replacement.proposal_id,
                        )
                    results.append(
                        KnowledgeMigrationResultItem(
                            proposal_id=item.proposal_id,
                            status="auto_applied",
                            replacement_proposal_id=replacement.proposal_id,
                        )
                    )
                elif item.disposition in {"retire", "block"}:
                    self._retire_pending_proposal(
                        item.proposal_id,
                        reason_code=item.reason_codes[0],
                    )
                    results.append(
                        KnowledgeMigrationResultItem(
                            proposal_id=item.proposal_id,
                            status=("blocked" if item.disposition == "block" else "retired"),
                            reason_code=item.reason_codes[0],
                        )
                    )
                else:
                    results.append(
                        KnowledgeMigrationResultItem(
                            proposal_id=item.proposal_id,
                            status="review",
                            reason_code=item.reason_codes[0],
                        )
                    )
            except (
                DocumentParseError,
                FileNotFoundError,
                KeyError,
                KnowledgeConflictError,
                KnowledgeProjectionError,
                KnowledgeStoreError,
                ValueError,
            ) as exc:
                results.append(
                    KnowledgeMigrationResultItem(
                        proposal_id=item.proposal_id,
                        status="error",
                        reason_code=_migration_error_code(exc),
                    )
                )
        return KnowledgeMigrationResult(plan_id=plan.plan_id, items=tuple(results))

    def _plan_pending_proposal(self, proposal: KnowledgeProposal) -> KnowledgeMigrationItem:
        base = {
            "proposal_id": proposal.proposal_id,
            "source_root_id": proposal.source_root_id,
            "source_relative_path": proposal.source_relative_path,
            "source_revision": proposal.source_revision,
        }
        if proposal.change_kind != "ingest":
            return KnowledgeMigrationItem(
                **base,
                disposition="review",
                reason_codes=("non_ingest_proposal",),
            )
        try:
            source = self.load_source(
                proposal.source_root_id,
                proposal.source_relative_path,
            )
        except FileNotFoundError:
            return KnowledgeMigrationItem(
                **base,
                disposition="retire",
                reason_codes=("source_missing",),
            )
        except KeyError:
            return KnowledgeMigrationItem(
                **base,
                disposition="review",
                reason_codes=("source_root_unavailable",),
            )
        except ValueError as exc:
            return KnowledgeMigrationItem(
                **base,
                disposition="block",
                reason_codes=(_migration_error_code(exc),),
            )
        if source.source_revision != proposal.source_revision:
            return KnowledgeMigrationItem(
                **base,
                disposition="retire",
                reason_codes=("source_revision_superseded",),
                current_source_revision=source.source_revision,
            )
        if proposal.parse_artifact_id is not None:
            try:
                artifact = self.get_parse_artifact(proposal.proposal_id)
            except KnowledgeStoreError:
                return KnowledgeMigrationItem(
                    **base,
                    disposition="block",
                    reason_codes=("invalid_parse_evidence",),
                    current_source_revision=source.source_revision,
                )
            if artifact is None:
                return KnowledgeMigrationItem(
                    **base,
                    disposition="block",
                    reason_codes=("missing_parse_evidence",),
                    current_source_revision=source.source_revision,
                )
            parser_id = artifact.document.provenance.parser_id
            return KnowledgeMigrationItem(
                **base,
                disposition=("auto_apply" if is_trusted_local_parser(parser_id) else "review"),
                reason_codes=(
                    "trusted_local_parser"
                    if is_trusted_local_parser(parser_id)
                    else "external_parser_output",
                ),
                parser_id=parser_id,
                parser_version=artifact.document.provenance.parser_version,
                current_source_revision=source.source_revision,
            )
        try:
            document = self.parser_registry.parse(source.parse_request())
        except DocumentRequiresOcrError:
            return KnowledgeMigrationItem(
                **base,
                disposition="review",
                reason_codes=("external_parser_required",),
                current_source_revision=source.source_revision,
            )
        except DocumentParseError:
            return KnowledgeMigrationItem(
                **base,
                disposition="review",
                reason_codes=("local_parse_failed",),
                current_source_revision=source.source_revision,
            )
        except (ParserNotFoundError, ParserConflictError):
            return KnowledgeMigrationItem(
                **base,
                disposition="review",
                reason_codes=("local_parser_unavailable",),
                current_source_revision=source.source_revision,
            )
        return KnowledgeMigrationItem(
            **base,
            disposition=(
                "auto_apply"
                if is_trusted_local_parser(document.provenance.parser_id)
                else "review"
            ),
            reason_codes=(
                "trusted_local_reparse"
                if is_trusted_local_parser(document.provenance.parser_id)
                else "external_parser_output",
            ),
            parser_id=document.provenance.parser_id,
            parser_version=document.provenance.parser_version,
            current_source_revision=source.source_revision,
        )

    def _retire_pending_proposal(
        self,
        proposal_id: str,
        *,
        reason_code: str,
        replacement_proposal_id: str | None = None,
    ) -> None:
        current = self.get_proposal(proposal_id)
        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE knowledge_proposals
                SET status='rejected', revision=revision+1,
                    projection_status='complete', error=?, updated_at=?
                WHERE proposal_id=? AND status='pending' AND revision=?
                """,
                (
                    f"migration:{reason_code}",
                    _now(),
                    current.proposal_id,
                    current.revision,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise KnowledgeConflictError("knowledge migration proposal changed")
            detail = {"reason_code": reason_code}
            if replacement_proposal_id is not None:
                detail["replacement_proposal_id"] = replacement_proposal_id
            self._event(
                connection,
                current.proposal_id,
                "migration_retired",
                current.revision + 1,
                detail,
            )
            connection.commit()

    def list_events(self, proposal_id: str) -> list[KnowledgeEvent]:
        self.initialize()
        self.get_proposal(proposal_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM knowledge_events
                WHERE proposal_id=? ORDER BY created_at, event_id LIMIT 500
                """,
                (_bounded_id(proposal_id),),
            ).fetchall()
        return [
            KnowledgeEvent(
                event_id=str(row["event_id"]),
                proposal_id=str(row["proposal_id"]),
                event_type=str(row["event_type"]),
                revision=int(row["revision"]),
                detail={
                    str(key): str(value)
                    for key, value in json.loads(str(row["detail_json"])).items()
                },
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def get_policy_decision(self, proposal_id: str) -> KnowledgePolicyDecision | None:
        """Return the persisted autonomy decision for a proposal, if evaluated."""

        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_policy_decisions WHERE proposal_id=?",
                (_bounded_id(proposal_id),),
            ).fetchone()
        return _policy_decision(row) if row is not None else None

    def evaluate_and_apply_policy(self, proposal_id: str) -> KnowledgeProposal:
        """Persist one deterministic decision and apply only its allowed transition."""

        proposal = self.get_proposal(proposal_id)
        decision = self.get_policy_decision(proposal_id)
        if decision is None and proposal.status != "pending":
            # Never relabel a historical human decision as an autonomous action.
            return proposal
        if decision is None:
            artifact = self.get_parse_artifact(proposal_id)
            learning = self.get_evidence_learning(proposal_id)
            visibility = _proposal_visibility(proposal.proposed_content)
            outcome = evaluate_knowledge_policy(
                KnowledgePolicyInput(
                    change_kind=proposal.change_kind,
                    source_kind=proposal.source_kind,
                    target_path=proposal.target_path,
                    visibility=visibility,
                    parser_id=(artifact.document.provenance.parser_id if artifact else None),
                    evidence_verified=(
                        self._evidence_learning_is_current(learning)
                        if learning is not None
                        else False
                    ),
                    evidence_count=(len(learning.citations) if learning is not None else 0),
                    generator_id=(learning.generator_id if learning is not None else None),
                )
            )
            now = _now()
            decision_id = "kpol_" + hashlib.sha256(
                f"{POLICY_ID}\0{POLICY_VERSION}\0{proposal.proposal_id}".encode()
            ).hexdigest()[:32]
            with self._lock, self._exclusive_lock(), self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                inserted = connection.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_policy_decisions (
                        decision_id, proposal_id, policy_id, policy_version,
                        risk_level, action, reason_codes_json, base_page_revision,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        decision_id,
                        proposal.proposal_id,
                        POLICY_ID,
                        POLICY_VERSION,
                        outcome.risk_level,
                        outcome.action,
                        json.dumps(outcome.reason_codes, sort_keys=True),
                        proposal.base_page_revision,
                        now,
                        now,
                    ),
                )
                if inserted.rowcount == 1:
                    self._event(
                        connection,
                        proposal.proposal_id,
                        "policy_evaluated",
                        proposal.revision,
                        {
                            "policy_id": POLICY_ID,
                            "policy_version": POLICY_VERSION,
                            "risk_level": outcome.risk_level,
                            "action": outcome.action,
                            "reason_codes": ",".join(outcome.reason_codes),
                        },
                    )
                connection.commit()
            decision = self.get_policy_decision(proposal_id)
        if decision is None:
            raise KnowledgeStoreError("knowledge policy decision could not be persisted")

        current = self.get_proposal(proposal_id)
        if decision.action == "auto_apply":
            if current.status == "pending":
                try:
                    current = self.approve(proposal_id, current.revision)
                except KnowledgeConflictError:
                    current = self.get_proposal(proposal_id)
                    if current.status != "approved" or current.projection_status != "complete":
                        raise
            if current.status != "approved" or current.projection_status != "complete":
                return current
            applied_revision = self._projection_revision_for_proposal(proposal_id)
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE knowledge_policy_decisions
                    SET applied_page_revision=?, updated_at=?
                    WHERE proposal_id=? AND applied_page_revision IS NULL
                    """,
                    (applied_revision, _now(), proposal_id),
                )
                connection.commit()
            return self.get_proposal(proposal_id)

        if decision.action == "block" and current.status == "pending":
            rejected = self.reject(proposal_id, current.revision)
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE knowledge_proposals SET error=?, updated_at=? WHERE proposal_id=?
                    """,
                    ("blocked by knowledge autonomy policy", _now(), proposal_id),
                )
                self._event(
                    connection,
                    proposal_id,
                    "policy_blocked",
                    rejected.revision,
                    {"reason_codes": ",".join(decision.reason_codes)},
                )
                connection.commit()
        return self.get_proposal(proposal_id)

    def approve(self, proposal_id: str, expected_revision: int) -> KnowledgeProposal:
        self.initialize()
        with self._lock, self._exclusive_lock():
            current = self.get_proposal(proposal_id)
            if current.status == "approved":
                if current.revision != expected_revision:
                    raise KnowledgeConflictError("proposal revision conflict")
                if current.projection_status != "complete":
                    self._project(current)
                return self.get_proposal(proposal_id)
            if current.status != "pending" or current.revision != expected_revision:
                raise KnowledgeConflictError("proposal revision conflict")
            self._assert_projection_base(current)
            now = _now()
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    UPDATE knowledge_proposals
                    SET status='approved', revision=revision+1,
                        projection_status='pending', error=NULL, updated_at=?
                    WHERE proposal_id=? AND status='pending' AND revision=?
                    """,
                    (now, proposal_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise KnowledgeConflictError("proposal revision conflict")
                self._event(connection, proposal_id, "proposal_approved", expected_revision + 1)
                connection.commit()
            approved = self.get_proposal(proposal_id)
            self._project(approved)
            return self.get_proposal(proposal_id)

    def reject(self, proposal_id: str, expected_revision: int) -> KnowledgeProposal:
        self.initialize()
        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE knowledge_proposals
                SET status='rejected', revision=revision+1,
                    projection_status='complete', error=NULL, updated_at=?
                WHERE proposal_id=? AND status='pending' AND revision=?
                """,
                (_now(), _bounded_id(proposal_id), expected_revision),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise KnowledgeConflictError("proposal revision conflict")
            self._event(connection, proposal_id, "proposal_rejected", expected_revision + 1)
            connection.commit()
        return self.get_proposal(proposal_id)

    def list_pages(self) -> list[KnowledgePage]:
        self.initialize()
        with self._connect() as connection:
            pages = connection.execute(
                "SELECT * FROM knowledge_pages ORDER BY title, page_id LIMIT 500"
            ).fetchall()
            result: list[KnowledgePage] = []
            for page in pages:
                revisions = connection.execute(
                    """
                    SELECT * FROM knowledge_page_revisions
                    WHERE page_id=? ORDER BY sequence LIMIT 100
                    """,
                    (page["page_id"],),
                ).fetchall()
                result.append(
                    KnowledgePage(
                        page_id=str(page["page_id"]),
                        path=str(page["path"]),
                        title=str(page["title"]),
                        current_revision=str(page["current_revision"]),
                        updated_at=str(page["updated_at"]),
                        revisions=tuple(_page_revision(row) for row in revisions),
                    )
                )
        return result

    def get_page_document(self, page_id: str) -> KnowledgePageDocument:
        """Load the current canonical Wiki revision without reading a source path."""

        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT page.page_id, page.path, page.title, page.updated_at,
                       revision.revision_id, revision.sequence, revision.content_hash,
                       revision.content, revision.source_revision, revision.proposal_id,
                       revision.change_kind, revision.git_commit, revision.created_at
                FROM knowledge_pages AS page
                JOIN knowledge_page_revisions AS revision
                  ON revision.revision_id = page.current_revision
                WHERE page.page_id=?
                """,
                (_bounded_id(page_id),),
            ).fetchone()
        if row is None:
            raise KeyError(page_id)
        return KnowledgePageDocument(
            page_id=str(row["page_id"]),
            path=str(row["path"]),
            title=str(row["title"]),
            updated_at=str(row["updated_at"]),
            revision=_page_revision(row),
            content=str(row["content"]),
        )

    def index_summary(self) -> KnowledgeIndexSummary:
        self.initialize()
        with self._connect() as connection:
            return self.knowledge_index.summary(connection)

    def rebuild_index(self) -> KnowledgeIndexSummary:
        """Recreate local retrieval projections from canonical page revisions."""

        self.initialize()
        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.knowledge_index.backfill(connection, force=True)
            connection.commit()
        return self.index_summary()

    def graph_status(self) -> KnowledgeGraphSnapshot | None:
        self.initialize()
        with self._connect() as connection:
            return self.knowledge_graph.status(connection)

    def rebuild_graph(self, *, force: bool = False) -> KnowledgeGraphSnapshot:
        """Rebuild one immutable graph projection from current Wiki revisions."""

        self.initialize()
        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                snapshot = self.knowledge_graph.rebuild(connection, force=force)
            except KnowledgeGraphError:
                connection.commit()
                raise
            connection.commit()
        return snapshot

    def graph_overview(
        self,
        *,
        graph_revision: str | None = None,
        query: str = "",
        kinds: tuple[str, ...] = (),
        offset: int = 0,
        limit: int = 500,
        edge_limit: int = 1_000,
    ) -> KnowledgeGraphOverview:
        self.initialize()
        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                overview = self.knowledge_graph.overview(
                    connection,
                    graph_revision=graph_revision,
                    query=query,
                    kinds=kinds,
                    offset=offset,
                    limit=limit,
                    edge_limit=edge_limit,
                )
            except KnowledgeGraphError:
                connection.commit()
                raise
            connection.commit()
        return overview

    def graph_node(
        self, node_id: str, *, graph_revision: str | None = None
    ) -> tuple[KnowledgeGraphSnapshot, KnowledgeGraphNode]:
        self.initialize()
        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = self.knowledge_graph.node(
                    connection, node_id, graph_revision=graph_revision
                )
            except KnowledgeGraphError:
                connection.commit()
                raise
            connection.commit()
        return result

    def graph_neighborhood(
        self,
        node_id: str,
        *,
        graph_revision: str | None = None,
        limit: int = 100,
    ) -> KnowledgeGraphNeighborhood:
        self.initialize()
        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = self.knowledge_graph.neighborhood(
                    connection,
                    node_id,
                    graph_revision=graph_revision,
                    limit=limit,
                )
            except KnowledgeGraphError:
                connection.commit()
                raise
            connection.commit()
        return result

    def learning_goal(self) -> LearningGoal:
        self.initialize()
        with self._lock:
            return self._read_learning_goal()

    def update_learning_goal(
        self,
        definition: LearningGoalDefinition,
        *,
        expected_goal_revision: str,
    ) -> LearningGoal:
        """Commit an explicit user-authored goal without treating it as learned evidence."""

        self.initialize()
        with self._lock, self._exclusive_lock():
            path = self.workspace_root / "purpose.md"
            current_content = path.read_text(encoding="utf-8")
            current = self._read_learning_goal(content=current_content)
            if current.goal_revision != expected_goal_revision:
                raise KnowledgeConflictError("learning goal revision conflict")
            managed_status = self._git(
                "status", "--porcelain=v1", "--", "purpose.md"
            ).stdout.strip()
            if managed_status:
                raise KnowledgeConflictError("purpose document changed outside Sage")
            rendered = render_learning_goal(definition)
            if rendered == current_content:
                return current
            _atomic_write(path, rendered)
            try:
                self._git("add", "--", "purpose.md")
                self._git(
                    "-c",
                    "user.name=Sage Knowledge",
                    "-c",
                    "user.email=sage-knowledge@local",
                    "commit",
                    "--only",
                    "-m",
                    "knowledge: update learning goal",
                    "--",
                    "purpose.md",
                )
            except Exception:
                _atomic_write(path, current_content)
                subprocess.run(
                    ["git", "reset", "--", "purpose.md"],
                    cwd=self.workspace_root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                raise
            return self._read_learning_goal(content=rendered)

    def analyze_graph(
        self,
        *,
        graph_revision: str | None = None,
        force: bool = False,
    ) -> KnowledgeGraphAnalysis:
        self.initialize()
        goal = self.learning_goal()
        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                graph_snapshot = self.knowledge_graph.snapshot(connection, graph_revision)
                analysis = self.knowledge_graph_analyzer.analyze(
                    connection, graph_snapshot, goal, force=force
                )
            except (KnowledgeGraphError, KnowledgeGraphAnalysisError):
                connection.commit()
                raise
            connection.commit()
        return analysis

    def _read_learning_goal(self, *, content: str | None = None) -> LearningGoal:
        path = self.workspace_root / "purpose.md"
        if path.is_symlink():
            raise KnowledgeStoreError("purpose document must not be a symbolic link")
        document = content if content is not None else path.read_text(encoding="utf-8")
        commit = self._git("log", "-1", "--format=%H", "--", "purpose.md").stdout.strip()
        return parse_learning_goal(document, git_commit=commit)

    def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        visibility: str = "private",
        source_ids: tuple[str, ...] = (),
        page_revisions: tuple[str, ...] = (),
    ) -> tuple[KnowledgeSearchHit, ...]:
        self.initialize()
        with self._connect() as connection:
            return self.knowledge_index.search(
                connection,
                query,
                top_k=top_k,
                visibility=visibility,
                source_ids=source_ids,
                page_revisions=page_revisions,
            )

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 8,
        token_budget: int = 3_000,
        visibility: str = "private",
        source_ids: tuple[str, ...] = (),
        page_revisions: tuple[str, ...] = (),
    ) -> KnowledgeRetrievalBundle:
        """Return one bounded evidence bundle for API and Agent consumers."""

        hits = self.search(
            query,
            top_k=top_k,
            visibility=visibility,
            source_ids=source_ids,
            page_revisions=page_revisions,
        )
        return assemble_retrieval_bundle(query, hits, token_budget=token_budget)

    def citation(
        self,
        citation_id: str,
        *,
        visibility: str = "private",
    ) -> KnowledgeChunk:
        """Resolve one current indexed citation without reading its source path."""

        self.initialize()
        with self._connect() as connection:
            return self.knowledge_index.resolve_citations(
                connection,
                (citation_id,),
                visibility=visibility,
            )[0][1]

    def propose_rollback(
        self,
        page_id: str,
        *,
        target_revision_id: str,
        expected_page_revision: str,
    ) -> KnowledgeProposal:
        self.initialize()
        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            page = connection.execute(
                "SELECT * FROM knowledge_pages WHERE page_id=?", (_bounded_id(page_id),)
            ).fetchone()
            if page is None:
                connection.rollback()
                raise KeyError(page_id)
            if str(page["current_revision"]) != expected_page_revision:
                connection.rollback()
                raise KnowledgeConflictError("page revision conflict")
            target = connection.execute(
                """
                SELECT * FROM knowledge_page_revisions
                WHERE page_id=? AND revision_id=?
                """,
                (page_id, _bounded_id(target_revision_id)),
            ).fetchone()
            if target is None:
                connection.rollback()
                raise KeyError(target_revision_id)
            proposal_id = "kprop_" + uuid.uuid4().hex
            now = _now()
            connection.execute(
                """
                INSERT INTO knowledge_proposals (
                    proposal_id, source_id, source_root_id, source_kind,
                    source_relative_path, source_revision, raw_path, page_id,
                    target_path, title, proposed_content, base_page_revision,
                    change_kind, status, projection_status, revision, error,
                    created_at, updated_at
                ) VALUES (?, ?, 'knowledge', 'rollback', ?, ?, '', ?, ?, ?, ?, ?,
                          'rollback', 'pending', 'pending', 0, NULL, ?, ?)
                """,
                (
                    proposal_id,
                    f"rollback:{page_id}",
                    str(page["path"]),
                    f"revision:{target_revision_id}",
                    page_id,
                    str(page["path"]),
                    str(page["title"]),
                    str(target["content"]),
                    expected_page_revision,
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                proposal_id,
                "rollback_proposed",
                0,
                {"target_revision_id": target_revision_id},
            )
            connection.commit()
        return self.get_proposal(proposal_id)

    def undo_auto_apply(
        self, proposal_id: str, *, expected_page_revision: str
    ) -> KnowledgeProposal:
        """Undo an automatic projection without erasing its immutable history."""

        original = self.get_proposal(proposal_id)
        decision = self.get_policy_decision(proposal_id)
        if (
            decision is None
            or decision.action != "auto_apply"
            or decision.applied_page_revision is None
            or decision.undone_at is not None
        ):
            raise KnowledgeConflictError("proposal is not an undoable auto-apply")
        if expected_page_revision != decision.applied_page_revision:
            raise KnowledgeConflictError("page revision conflict")
        page = next(
            (item for item in self.list_pages() if item.page_id == original.page_id),
            None,
        )
        if page is None or page.current_revision != expected_page_revision:
            raise KnowledgeConflictError("page revision conflict")

        if decision.base_page_revision:
            undo = self.propose_rollback(
                original.page_id,
                target_revision_id=decision.base_page_revision,
                expected_page_revision=expected_page_revision,
            )
        else:
            undo = self._propose_retraction(original, expected_page_revision)
        self.evaluate_and_apply_policy(undo.proposal_id)
        approved = self.approve(undo.proposal_id, expected_revision=0)
        undo_revision = self._projection_revision_for_proposal(approved.proposal_id)
        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE knowledge_policy_decisions
                SET undo_proposal_id=?, undo_page_revision=?, undone_at=?, updated_at=?
                WHERE proposal_id=? AND undone_at IS NULL
                """,
                (
                    approved.proposal_id,
                    undo_revision,
                    _now(),
                    _now(),
                    original.proposal_id,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise KnowledgeConflictError("auto-apply was already undone")
            self._event(
                connection,
                original.proposal_id,
                "auto_apply_undone",
                original.revision,
                {
                    "undo_proposal_id": approved.proposal_id,
                    "undo_page_revision": undo_revision,
                },
            )
            connection.commit()
        return approved

    def _propose_retraction(
        self, original: KnowledgeProposal, expected_page_revision: str
    ) -> KnowledgeProposal:
        proposal_id = "kprop_" + uuid.uuid4().hex
        now = _now()
        content = _retraction_page(original, proposal_id)
        with self._lock, self._exclusive_lock(), self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            page = connection.execute(
                "SELECT * FROM knowledge_pages WHERE page_id=?", (original.page_id,)
            ).fetchone()
            if page is None or str(page["current_revision"]) != expected_page_revision:
                connection.rollback()
                raise KnowledgeConflictError("page revision conflict")
            connection.execute(
                """
                INSERT INTO knowledge_proposals (
                    proposal_id, source_id, source_root_id, source_kind,
                    source_relative_path, source_revision, raw_path, page_id,
                    target_path, title, proposed_content, base_page_revision,
                    change_kind, status, projection_status, revision,
                    parse_artifact_id, error, created_at, updated_at
                ) VALUES (?, ?, 'knowledge', 'retraction', ?, ?, '', ?, ?, ?, ?, ?,
                          'retraction', 'pending', 'pending', 0, NULL, NULL, ?, ?)
                """,
                (
                    proposal_id,
                    f"retraction:{original.proposal_id}",
                    original.source_relative_path,
                    f"retracted:{original.source_revision}",
                    original.page_id,
                    original.target_path,
                    original.title,
                    content,
                    expected_page_revision,
                    now,
                    now,
                ),
            )
            self._event(
                connection,
                proposal_id,
                "retraction_proposed",
                0,
                {"retracted_proposal_id": original.proposal_id},
            )
            connection.commit()
        return self.get_proposal(proposal_id)

    def _projection_revision_for_proposal(self, proposal_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision_id FROM knowledge_page_revisions WHERE proposal_id=?",
                (_bounded_id(proposal_id),),
            ).fetchone()
        if row is None:
            raise KnowledgeStoreError("knowledge projection revision is missing")
        return str(row["revision_id"])

    def proposal_diff(self, proposal: KnowledgeProposal) -> str:
        current_path = self.workspace_root / proposal.target_path
        current = current_path.read_text(encoding="utf-8") if current_path.is_file() else ""
        return "".join(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                proposal.proposed_content.splitlines(keepends=True),
                fromfile=proposal.target_path,
                tofile=proposal.target_path,
            )
        )

    def _project(self, proposal: KnowledgeProposal) -> None:
        self._assert_projection_base(proposal)
        page_path = self.workspace_root / proposal.target_path
        index_path = self.workspace_root / "index.md"
        log_path = self.workspace_root / "log.md"
        backups = {path: _optional_text(path) for path in (page_path, index_path, log_path)}
        now = _now()
        try:
            _atomic_write(page_path, proposal.proposed_content)
            index = backups[index_path] or "# Knowledge Index\n\n## Sources\n"
            link = f"- [[{proposal.target_path}|{proposal.title}]] — {proposal.source_kind}\n"
            if proposal.target_path not in index:
                index = index.rstrip() + "\n" + link
            _atomic_write(index_path, index)
            log = backups[log_path] or "# Knowledge Log\n"
            log += (
                f"\n## [{now}] {proposal.change_kind} | {proposal.title}\n\n"
                f"- proposal: `{proposal.proposal_id}`\n"
                f"- source revision: `{proposal.source_revision}`\n"
                f"- target: `[[{proposal.target_path}]]`\n"
            )
            _atomic_write(log_path, log)
            paths = [proposal.target_path, "index.md", "log.md"]
            if proposal.raw_path:
                paths.append(proposal.raw_path)
            self._git("add", "--", *paths)
            self._git(
                "-c",
                "user.name=Sage Knowledge",
                "-c",
                "user.email=sage-knowledge@local",
                "commit",
                "-m",
                f"knowledge: apply {proposal.proposal_id}",
            )
            commit = self._git("rev-parse", "HEAD").stdout.strip()
            self._record_projection(proposal, commit, now)
        except Exception as exc:
            for path, content in backups.items():
                _restore(path, content)
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE knowledge_proposals
                    SET projection_status='error', error=?, updated_at=?
                    WHERE proposal_id=?
                    """,
                    ("knowledge projection failed", _now(), proposal.proposal_id),
                )
                connection.commit()
            if isinstance(exc, KnowledgeConflictError):
                raise
            raise KnowledgeProjectionError("knowledge projection failed") from exc

    def _record_projection(
        self, proposal: KnowledgeProposal, git_commit: str, created_at: str
    ) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            page = connection.execute(
                "SELECT current_revision FROM knowledge_pages WHERE page_id=?",
                (proposal.page_id,),
            ).fetchone()
            actual_base = str(page["current_revision"]) if page else ""
            if actual_base != proposal.base_page_revision:
                connection.rollback()
                raise KnowledgeConflictError("page revision changed during projection")
            sequence = (
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM knowledge_page_revisions WHERE page_id=?",
                        (proposal.page_id,),
                    ).fetchone()[0]
                )
                + 1
            )
            revision_id = "krev_" + uuid.uuid4().hex
            content_hash = _content_hash(proposal.proposed_content)
            connection.execute(
                """
                INSERT INTO knowledge_page_revisions (
                    revision_id, page_id, sequence, content_hash, content,
                    source_revision, proposal_id, change_kind, git_commit, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    proposal.page_id,
                    sequence,
                    content_hash,
                    proposal.proposed_content,
                    proposal.source_revision,
                    proposal.proposal_id,
                    proposal.change_kind,
                    git_commit,
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO knowledge_pages (page_id, path, title, current_revision, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(page_id) DO UPDATE SET
                    path=excluded.path,
                    title=excluded.title,
                    current_revision=excluded.current_revision,
                    updated_at=excluded.updated_at
                """,
                (
                    proposal.page_id,
                    proposal.target_path,
                    proposal.title,
                    revision_id,
                    created_at,
                ),
            )
            connection.execute(
                """
                UPDATE knowledge_proposals
                SET projection_status='complete', error=NULL, updated_at=?
                WHERE proposal_id=? AND status='approved'
                """,
                (created_at, proposal.proposal_id),
            )
            self._event(
                connection,
                proposal.proposal_id,
                "projection_completed",
                proposal.revision,
                {"page_revision": revision_id, "git_commit": git_commit},
            )
            self.knowledge_index.sync_revision_safely(connection, revision_id)
            connection.commit()

    def _assert_projection_base(self, proposal: KnowledgeProposal) -> None:
        managed_status = self._git(
            "status",
            "--porcelain=v1",
            "--",
            "index.md",
            "log.md",
            proposal.target_path,
        ).stdout.strip()
        if managed_status:
            raise KnowledgeConflictError("page changed outside Sage")
        with self._connect() as connection:
            page = connection.execute(
                "SELECT * FROM knowledge_pages WHERE page_id=?", (proposal.page_id,)
            ).fetchone()
            if page is None:
                if proposal.base_page_revision:
                    raise KnowledgeConflictError("proposal base revision is stale")
                if (self.workspace_root / proposal.target_path).exists():
                    initial_overview = (
                        proposal.change_kind == "synthesis"
                        and proposal.target_path == "overview.md"
                        and (self.workspace_root / proposal.target_path).read_text(
                            encoding="utf-8"
                        )
                        == _INITIAL_OVERVIEW
                    )
                    if not initial_overview:
                        raise KnowledgeConflictError("page changed outside Sage")
                return
            if str(page["current_revision"]) != proposal.base_page_revision:
                raise KnowledgeConflictError("proposal base revision is stale")
            revision = connection.execute(
                "SELECT content_hash FROM knowledge_page_revisions WHERE revision_id=?",
                (page["current_revision"],),
            ).fetchone()
        page_path = self.workspace_root / proposal.target_path
        if not page_path.is_file() or page_path.is_symlink():
            raise KnowledgeConflictError("page changed outside Sage")
        actual = _content_hash(page_path.read_text(encoding="utf-8"))
        if revision is None or actual != str(revision["content_hash"]):
            raise KnowledgeConflictError("page changed outside Sage")

    def _prepare_workspace(self) -> None:
        if self.workspace_root.exists() and self.workspace_root.is_symlink():
            raise ValueError("knowledge workspace must not be a symbolic link")
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        for directory in (
            "raw/sources",
            "wiki/sources",
            "wiki/projects",
            "wiki/concepts",
            "wiki/decisions",
            "wiki/queries",
            "wiki/learning",
            "reviews",
        ):
            (self.workspace_root / directory).mkdir(parents=True, exist_ok=True)
        defaults = {
            "purpose.md": default_learning_goal_content(),
            "schema.md": "# Schema\n\n所有 Wiki 写入必须来自 proposal，并保留 source revision。\n",
            "overview.md": _INITIAL_OVERVIEW,
            "index.md": "# Knowledge Index\n\n## Sources\n",
            "log.md": "# Knowledge Log\n",
        }
        for relative, content in defaults.items():
            path = self.workspace_root / relative
            if not path.exists():
                _atomic_write(path, content)
        if not (self.workspace_root / ".git").exists():
            self._git("init", "-b", "main")
        if not self._has_git_head():
            self._git("add", "--", *defaults)
            self._git(
                "-c",
                "user.name=Sage Knowledge",
                "-c",
                "user.email=sage-knowledge@local",
                "commit",
                "-m",
                "chore: initialize knowledge workspace",
            )

    def _prepare_database(self) -> None:
        if self.database_path.exists() and self.database_path.is_symlink():
            raise ValueError("knowledge database must not be a symbolic link")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        if self.database_path.parent.is_symlink():
            raise ValueError("knowledge database directory must not be a symbolic link")
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, 1, 2, 3, 4, 5, 6, 7, 8, _SCHEMA_VERSION}:
                raise KnowledgeStoreError(f"unsupported knowledge schema version {version}")
            connection.executescript(_SCHEMA)
            proposal_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(knowledge_proposals)")
            }
            if "parse_artifact_id" not in proposal_columns:
                connection.execute(
                    "ALTER TABLE knowledge_proposals ADD COLUMN parse_artifact_id TEXT"
                )
            self._backfill_source_understandings(connection)
            self.knowledge_index.ensure_schema(connection)
            self.knowledge_index.backfill(connection)
            self.knowledge_graph.ensure_schema(connection)
            self.knowledge_graph_analyzer.ensure_schema(connection)
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            connection.commit()

    @staticmethod
    def _backfill_source_understandings(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            """
            SELECT artifact_id, payload_json, created_at
            FROM knowledge_parse_artifacts
            WHERE artifact_id NOT IN (
                SELECT artifact_id FROM knowledge_source_understandings
            )
            ORDER BY created_at, artifact_id
            """
        ).fetchall()
        for row in rows:
            try:
                document = deserialize_document(str(row["payload_json"]))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise KnowledgeStoreError("parse artifact integrity check failed") from exc
            understanding = understand_source(str(row["artifact_id"]), document)
            payload_json = serialize_understanding(understanding)
            connection.execute(
                """
                INSERT INTO knowledge_source_understandings (
                    understanding_id, artifact_id, source_id, source_revision,
                    generator_id, generator_version, payload_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    understanding.understanding_id,
                    understanding.artifact_id,
                    understanding.source_id,
                    understanding.source_revision,
                    understanding.generator_id,
                    understanding.generator_version,
                    hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
                    payload_json,
                    str(row["created_at"]),
                ),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        lock_path = self.database_path.with_suffix(self.database_path.suffix + ".lock")
        if lock_path.exists() and lock_path.is_symlink():
            raise ValueError("knowledge lock must not be a symbolic link")
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            _lock_descriptor(descriptor)
            yield
        finally:
            _unlock_descriptor(descriptor)
            os.close(descriptor)

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", *args],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise KnowledgeProjectionError(result.stderr.strip() or "git command failed")
        return result

    def _has_git_head(self) -> bool:
        return (
            subprocess.run(
                ["git", "rev-parse", "--verify", "HEAD"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=False,
            ).returncode
            == 0
        )

    @staticmethod
    def _validate_source_roots(
        roots: Mapping[str, KnowledgeSourceRoot],
    ) -> dict[str, KnowledgeSourceRoot]:
        validated: dict[str, KnowledgeSourceRoot] = {}
        for key, value in roots.items():
            if key != value.root_id or not _ROOT_ID.fullmatch(key):
                raise ValueError("invalid knowledge source root id")
            if value.kind not in {"obsidian", "markdown", "github", "feishu", "web"}:
                raise ValueError("invalid knowledge source kind")
            path = value.path.expanduser().resolve()
            if not path.is_dir() or value.path.is_symlink():
                raise ValueError("knowledge source root must be a regular directory")
            validated[key] = KnowledgeSourceRoot(
                root_id=key,
                kind=value.kind,
                label=value.label.strip()[:120] or key,
                path=path,
            )
        return validated

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        proposal_id: str,
        event_type: str,
        revision: int,
        detail: dict[str, str] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO knowledge_events (
                event_id, proposal_id, event_type, revision, detail_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "kevt_" + uuid.uuid4().hex,
                proposal_id,
                event_type,
                revision,
                json.dumps(detail or {}, sort_keys=True),
                _now(),
            ),
        )


def _proposal(row: sqlite3.Row) -> KnowledgeProposal:
    return KnowledgeProposal(
        proposal_id=str(row["proposal_id"]),
        source_id=str(row["source_id"]),
        source_root_id=str(row["source_root_id"]),
        source_kind=str(row["source_kind"]),
        source_relative_path=str(row["source_relative_path"]),
        source_revision=str(row["source_revision"]),
        raw_path=str(row["raw_path"]),
        page_id=str(row["page_id"]),
        target_path=str(row["target_path"]),
        title=str(row["title"]),
        proposed_content=str(row["proposed_content"]),
        base_page_revision=str(row["base_page_revision"]),
        change_kind=str(row["change_kind"]),
        status=str(row["status"]),
        projection_status=str(row["projection_status"]),
        revision=int(row["revision"]),
        parse_artifact_id=(
            str(row["parse_artifact_id"]) if row["parse_artifact_id"] is not None else None
        ),
        error=str(row["error"]) if row["error"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _policy_decision(row: sqlite3.Row) -> KnowledgePolicyDecision:
    reason_codes = json.loads(str(row["reason_codes_json"]))
    if not isinstance(reason_codes, list) or not all(
        isinstance(item, str) for item in reason_codes
    ):
        raise KnowledgeStoreError("knowledge policy decision is invalid")
    return KnowledgePolicyDecision(
        decision_id=str(row["decision_id"]),
        proposal_id=str(row["proposal_id"]),
        policy_id=str(row["policy_id"]),
        policy_version=str(row["policy_version"]),
        risk_level=str(row["risk_level"]),
        action=str(row["action"]),
        reason_codes=tuple(reason_codes),
        base_page_revision=str(row["base_page_revision"]),
        applied_page_revision=(
            str(row["applied_page_revision"])
            if row["applied_page_revision"] is not None
            else None
        ),
        undo_proposal_id=(
            str(row["undo_proposal_id"]) if row["undo_proposal_id"] is not None else None
        ),
        undo_page_revision=(
            str(row["undo_page_revision"])
            if row["undo_page_revision"] is not None
            else None
        ),
        undone_at=str(row["undone_at"]) if row["undone_at"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _page_revision(row: sqlite3.Row) -> KnowledgePageRevision:
    return KnowledgePageRevision(
        revision_id=str(row["revision_id"]),
        sequence=int(row["sequence"]),
        content_hash=str(row["content_hash"]),
        source_revision=str(row["source_revision"]),
        proposal_id=str(row["proposal_id"]),
        change_kind=str(row["change_kind"]),
        git_commit=str(row["git_commit"]),
        created_at=str(row["created_at"]),
    )


def _relative_source_path(value: str) -> PurePosixPath:
    normalized = value.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.suffix.lower() not in _SOURCE_FORMATS
    ):
        raise ValueError("invalid relative source path")
    return path


def _source_format(path: PurePosixPath) -> tuple[str, int]:
    try:
        return _SOURCE_FORMATS[path.suffix.lower()]
    except KeyError as exc:
        raise ValueError("unsupported knowledge source format") from exc


def _parse_artifact_id(
    source_id: str,
    source_revision: str,
    document: ParsedDocument,
) -> str:
    payload_json = serialize_document(document)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    return (
        "part_"
        + hashlib.sha256(
            (
                f"{source_id}\0{source_revision}\0"
                f"{document.provenance.parser_id}\0{document.provenance.parser_version}\0"
                f"{payload_hash}"
            ).encode()
        ).hexdigest()[:32]
    )


def _read_source_bytes(root: Path, path: Path, *, max_bytes: int) -> bytes:
    try:
        lexical_relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("invalid relative source path") from exc
    cursor = root
    for part in lexical_relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError("source must not be a symbolic link")
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("invalid relative source path") from exc
    if not resolved.is_file():
        raise FileNotFoundError(path)
    stat = resolved.stat()
    if stat.st_size > max_bytes:
        raise ValueError(f"source exceeds {max_bytes // (1024 * 1024)} MiB limit")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(resolved, flags)
    with os.fdopen(descriptor, "rb") as handle:
        payload = handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"source exceeds {max_bytes // (1024 * 1024)} MiB limit")
    return payload


def _scan_source_secrets(payload: bytes, media_type: str) -> None:
    if media_type not in {"text/markdown", "text/html", "application/xhtml+xml"}:
        return
    try:
        content = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("text source must be UTF-8") from exc
    _scan_text_secrets(content)


def _scan_text_secrets(content: str) -> None:
    if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
        raise ValueError("source may contain secret material")


def _source_page(
    *,
    page_id: str,
    title: str,
    source_id: str,
    source_kind: str,
    source_relative_path: str,
    source_revision: str,
    raw_path: str,
    content: str,
    parser_id: str,
    parser_version: str,
    parsed_document_id: str,
) -> str:
    if len(content.encode("utf-8")) > _MAX_PROPOSAL_BYTES:
        raise ValueError("proposal exceeds size limit")
    quoted_title = json.dumps(title, ensure_ascii=False)
    quoted_path = json.dumps(source_relative_path, ensure_ascii=False)
    return (
        "---\n"
        f"id: {page_id}\n"
        "type: source\n"
        f"title: {quoted_title}\n"
        "status: draft\n"
        "visibility: private\n"
        "sources:\n"
        f"  - source_id: {source_id}\n"
        f"    kind: {source_kind}\n"
        f"    path: {quoted_path}\n"
        f"    revision: {source_revision}\n"
        f"raw_snapshot: {raw_path}\n"
        f"parser_id: {parser_id}\n"
        f"parser_version: {parser_version}\n"
        f"parsed_document_id: {parsed_document_id}\n"
        "---\n\n"
        f"# {title}\n\n"
        "> 本页是可审核的来源投影。后续 LLM 综合必须继续保留来源 revision。\n\n"
        "## 来源内容\n\n"
        f"{content.rstrip()}\n"
    )


def _proposal_visibility(content: str) -> str:
    if not content.startswith("---\n"):
        return ""
    end = content.find("\n---\n", 4)
    if end < 0:
        return ""
    for line in content[4:end].splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "visibility":
            return value.strip().strip("'\"")
    return ""


def _retraction_page(original: KnowledgeProposal, undo_proposal_id: str) -> str:
    return (
        "---\n"
        f"id: {original.page_id}\n"
        "type: source\n"
        f"title: {json.dumps(original.title, ensure_ascii=False)}\n"
        "status: retracted\n"
        "visibility: private\n"
        f"retracted_proposal_id: {original.proposal_id}\n"
        f"retraction_proposal_id: {undo_proposal_id}\n"
        "---\n\n"
        f"# {original.title}\n\n"
        "> 此自动沉淀已由用户撤销。历史来源、提案和 Git revision 仍保留用于审计。\n"
    )


def _validate_parsed_document(request: ParseRequest, document: ParsedDocument) -> None:
    if (
        document.source_id != request.source_id
        or document.relative_path != request.relative_path
        or document.source_revision != request.source_revision
        or document.provenance.input_revision != request.source_revision
        or document.provenance.media_type != request.media_type
    ):
        raise KnowledgeStoreError("parser returned mismatched source provenance")
    if not document.document_id.startswith("pdoc_"):
        raise KnowledgeStoreError("parser returned invalid document identity")
    if (
        not document.title.strip()
        or len(document.title) > 500
        or "\n" in document.title
        or len(document.rendered_markdown.encode("utf-8")) > _MAX_PROPOSAL_BYTES
        or sum(len(block.text.encode("utf-8")) for block in document.blocks)
        > _MAX_PROPOSAL_BYTES * 2
    ):
        raise KnowledgeStoreError("parser returned oversized document content")
    if not document.provenance.parser_id or not document.provenance.parser_version:
        raise KnowledgeStoreError("parser returned incomplete provenance")
    block_ids: set[str] = set()
    for ordinal, block in enumerate(document.blocks):
        media_path = PurePosixPath(block.media_ref) if block.media_ref else None
        if (
            block.ordinal != ordinal
            or not block.block_id.startswith("pblk_")
            or block.block_id in block_ids
            or not 0.0 <= block.confidence <= 1.0
            or (
                media_path is not None
                and (
                    media_path.is_absolute()
                    or any(part in {"", ".", ".."} for part in media_path.parts)
                )
            )
        ):
            raise KnowledgeStoreError("parser returned invalid block contract")
        block_ids.add(block.block_id)


def _write_immutable_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.is_symlink() or path.read_bytes() != content:
            raise KnowledgeConflictError("immutable raw snapshot conflict")
        return
    if path.is_symlink():
        raise ValueError("knowledge file must not be a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_write(path: Path, content: str) -> None:
    if path.exists() and path.is_symlink():
        raise ValueError("knowledge file must not be a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _restore(path: Path, content: str | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
    else:
        _atomic_write(path, content)


def _optional_text(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise ValueError("knowledge file path is unsafe")
    return path.read_text(encoding="utf-8")


def _title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()[:160]
    return fallback.strip()[:160] or "Untitled Source"


def _slug(value: str) -> str:
    normalized = re.sub(r"[^\w-]+", "-", value, flags=re.UNICODE).strip("-").lower()
    return normalized[:96] or "source"


def _content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def _bounded_id(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("invalid knowledge identifier")
    return normalized


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _migration_error_code(exc: Exception) -> str:
    if isinstance(exc, DocumentRequiresOcrError):
        return "external_parser_required"
    if isinstance(exc, FileNotFoundError):
        return "source_missing"
    if isinstance(exc, KeyError):
        return "source_root_unavailable"
    if isinstance(exc, KnowledgeProjectionError):
        return "projection_failed"
    if isinstance(exc, KnowledgeConflictError):
        return "revision_conflict"
    if isinstance(exc, DocumentParseError):
        return "local_parse_failed"
    if isinstance(exc, ValueError) and "secret material" in str(exc):
        return "sensitive_content"
    return "migration_failed"


def _lock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        locker = import_module("msvcrt")
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        locker.locking(descriptor, locker.LK_LOCK, 1)
        return
    locker = import_module("fcntl")
    locker.flock(descriptor, locker.LOCK_EX)


def _unlock_descriptor(descriptor: int) -> None:
    if os.name == "nt":
        locker = import_module("msvcrt")
        os.lseek(descriptor, 0, os.SEEK_SET)
        locker.locking(descriptor, locker.LK_UNLCK, 1)
        return
    locker = import_module("fcntl")
    locker.flock(descriptor, locker.LOCK_UN)
