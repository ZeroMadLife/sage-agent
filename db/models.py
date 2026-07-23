"""SQLAlchemy models for persistent chat history."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Base class for database models."""


class SessionRecord(Base):
    """会话记录 — 每次对话创建一条。"""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
    )
    status: Mapped[str] = mapped_column(String(20), default="active")


class MessageRecord(Base):
    """消息记录 — 每条用户/助手/system 消息。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sessions.id"),
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    tool_calls_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ItineraryRecord(Base):
    """行程归档 — 每次生成的行程保存。"""

    __tablename__ = "itineraries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sessions.id"),
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    destination: Mapped[str] = mapped_column(String(100))
    content_json: Mapped[str] = mapped_column(Text)
    total_cost: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CloudUserRecord(Base):
    """An invited user in the V7 cloud control plane."""

    __tablename__ = "cloud_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class AuthIdentityRecord(Base):
    """A provider subject mapped to one Sage user."""

    __tablename__ = "cloud_auth_identities"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_subject", name="cloud_identity_provider_subject_key"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cloud_users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    provider_subject: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CloudInviteRecord(Base):
    """One-time invite represented by a digest rather than its raw code."""

    __tablename__ = "cloud_invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("cloud_users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CloudLoginSessionRecord(Base):
    """Server-side session record; the browser token itself is never persisted."""

    __tablename__ = "cloud_login_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cloud_users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_name: Mapped[str] = mapped_column(String(120), default="Unknown device")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CloudOAuthTransactionRecord(Base):
    """Short-lived, one-time OAuth transaction with encrypted private state."""

    __tablename__ = "cloud_oauth_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), index=True)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    browser_binding_hash: Mapped[str] = mapped_column(String(64), index=True)
    encrypted_payload: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CloudProviderCredentialRecord(Base):
    """Encrypted provider credential; plaintext tokens never enter browser state."""

    __tablename__ = "cloud_provider_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="cloud_credential_user_provider_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cloud_users.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    scopes: Mapped[str] = mapped_column(Text, default="")
    provider_login: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CloudModelProviderRecord(Base):
    """Owner-scoped encrypted LLM Provider configuration."""

    __tablename__ = "cloud_model_providers"
    __table_args__ = (
        UniqueConstraint("owner_user_id", "name", name="cloud_model_provider_owner_name_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cloud_users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    api_mode: Mapped[str] = mapped_column(String(40))
    base_url: Mapped[str] = mapped_column(String(500))
    encrypted_api_key: Mapped[str] = mapped_column(Text)
    key_hint: Mapped[str] = mapped_column(String(24), default="")
    status: Mapped[str] = mapped_column(String(24), default="untested")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CloudModelRecord(Base):
    """One model exposed by an owner-scoped LLM Provider."""

    __tablename__ = "cloud_models"
    __table_args__ = (
        UniqueConstraint("provider_id", "model_id", name="cloud_model_provider_model_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cloud_model_providers.id", ondelete="CASCADE"), index=True
    )
    model_id: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255))
    context_window_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_reserve_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CloudModelPreferenceRecord(Base):
    """Account default Provider/model selection."""

    __tablename__ = "cloud_model_preferences"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cloud_users.id", ondelete="CASCADE"), primary_key=True
    )
    provider_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cloud_model_providers.id", ondelete="CASCADE"), index=True
    )
    model_record_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cloud_models.id", ondelete="CASCADE"), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CloudProjectRecord(Base):
    """Owner-scoped project metadata, separate from a filesystem checkout."""

    __tablename__ = "cloud_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cloud_users.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CloudWorkspaceRecord(Base):
    """Opaque workspace metadata; no server filesystem path is stored here."""

    __tablename__ = "cloud_workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("cloud_projects.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(32))
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="provisioning")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KnowledgeWorkspaceRecord(Base):
    """Tenant-ready metadata for one versioned Knowledge Workspace."""

    __tablename__ = "knowledge_workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KnowledgeSourceRecord(Base):
    """A configured source root without its server filesystem path."""

    __tablename__ = "knowledge_source_roots"
    __table_args__ = (
        UniqueConstraint("workspace_id", "root_id", name="knowledge_source_workspace_root_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_workspaces.id", ondelete="CASCADE"), index=True
    )
    root_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    label: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KnowledgeSourceManifestRecord(Base):
    """Last committed fingerprint for one source path."""

    __tablename__ = "knowledge_source_manifests"
    __table_args__ = (
        UniqueConstraint("source_id", "relative_path", name="knowledge_manifest_source_path_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_source_roots.id", ondelete="CASCADE"), index=True
    )
    relative_path: Mapped[str] = mapped_column(String(1024))
    source_revision: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(24), default="present", index=True)
    last_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KnowledgeSourceSyncRecord(Base):
    """Monotonic committed scan watermark for one source root."""

    __tablename__ = "knowledge_source_sync"

    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_source_roots.id", ondelete="CASCADE"), primary_key=True
    )
    watermark: Mapped[int] = mapped_column(Integer, default=0)
    manifest_hash: Mapped[str] = mapped_column(String(64), default="")
    adapter_id: Mapped[str] = mapped_column(String(64), default="")
    adapter_version: Mapped[str] = mapped_column(String(64), default="")
    adapter_checkpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    resume_cursor: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    scan_status: Mapped[str] = mapped_column(String(32), default="idle", index=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    last_scan_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_scan_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KnowledgeSyncPlanRecord(Base):
    """Immutable, replayable diff between two source manifests."""

    __tablename__ = "knowledge_sync_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_workspaces.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_source_roots.id", ondelete="CASCADE"), index=True
    )
    relative_directory: Mapped[str] = mapped_column(String(1024), default=".")
    pipeline_version: Mapped[str] = mapped_column(String(64))
    adapter_id: Mapped[str] = mapped_column(String(64), default="")
    adapter_version: Mapped[str] = mapped_column(String(64), default="")
    base_checkpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_checkpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    base_watermark: Mapped[int] = mapped_column(Integer, default=0)
    target_watermark: Mapped[int] = mapped_column(Integer, default=1)
    manifest_hash: Mapped[str] = mapped_column(String(64))
    changes_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="planned", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KnowledgeIngestJobRecord(Base):
    """PostgreSQL-authoritative state for one batch import."""

    __tablename__ = "knowledge_ingest_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_workspaces.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_source_roots.id", ondelete="CASCADE"), index=True
    )
    sync_plan_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("knowledge_sync_plans.id", ondelete="SET NULL"),
        unique=True,
        nullable=True,
    )
    relative_directory: Mapped[str] = mapped_column(String(1024), default=".")
    pipeline_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    processed_items: Mapped[int] = mapped_column(Integer, default=0)
    succeeded_items: Mapped[int] = mapped_column(Integer, default=0)
    skipped_items: Mapped[int] = mapped_column(Integer, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, default=0)
    cancelled_items: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KnowledgeIngestItemRecord(Base):
    """One independently retryable source revision in a batch import."""

    __tablename__ = "knowledge_ingest_items"
    __table_args__ = (
        UniqueConstraint("job_id", "relative_path", name="knowledge_item_job_path_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_ingest_jobs.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_source_roots.id", ondelete="CASCADE"), index=True
    )
    relative_path: Mapped[str] = mapped_column(String(1024))
    source_revision: Mapped[str] = mapped_column(String(80))
    change_kind: Mapped[str] = mapped_column(String(24), default="added")
    idempotency_key: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    proposal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KnowledgeExternalParseRecord(Base):
    """Server-only resumable parser ticket for one immutable ingest item."""

    __tablename__ = "knowledge_external_parse_tasks"
    __table_args__ = (
        UniqueConstraint(
            "adapter_id",
            "task_id",
            name="knowledge_external_parse_adapter_task_key",
        ),
    )

    item_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_ingest_items.id", ondelete="CASCADE"),
        primary_key=True,
    )
    adapter_id: Mapped[str] = mapped_column(String(64))
    adapter_version: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KnowledgeSourceProposalRecord(Base):
    """User-reviewable bridge from one private run artifact to Knowledge."""

    __tablename__ = "knowledge_source_proposals"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "owner_id",
            "thread_id",
            "run_id",
            "artifact_ref",
            "content_hash",
            name="knowledge_source_proposal_artifact_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_workspaces.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    thread_id: Mapped[str] = mapped_column(String(128), index=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    artifact_ref: Mapped[str] = mapped_column(String(1000))
    source_kind: Mapped[str] = mapped_column(String(32), default="web")
    canonical_url: Mapped[str] = mapped_column(String(2000))
    title: Mapped[str] = mapped_column(String(300))
    media_type: Mapped[str] = mapped_column(String(120))
    retrieved_at: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(String(1000), default="")
    evidence_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    target_root_id: Mapped[str] = mapped_column(String(64), default="web-evidence")
    target_relative_path: Mapped[str] = mapped_column(String(1024), default="")
    job_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("knowledge_ingest_jobs.id", ondelete="SET NULL"),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KnowledgeSourceProposalEventRecord(Base):
    """Append-only lifecycle evidence for one Knowledge source proposal."""

    __tablename__ = "knowledge_source_proposal_events"
    __table_args__ = (
        UniqueConstraint(
            "proposal_id",
            "sequence",
            name="knowledge_source_proposal_event_sequence_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_source_proposals.id", ondelete="CASCADE"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PublicPublicationCandidateRecord(Base):
    """Immutable public package candidate awaiting an explicit owner decision."""

    __tablename__ = "public_publication_candidates"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "package_id",
            "package_revision",
            name="public_publication_candidate_revision_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    package_id: Mapped[str] = mapped_column(String(128))
    package_revision: Mapped[str] = mapped_column(String(128), index=True)
    package_digest: Mapped[str] = mapped_column(String(64), index=True)
    package_json: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(1000), default="")
    evidence_refs_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    decided_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class PublicPublicationCandidateEventRecord(Base):
    """Append-only approval history for a public publication candidate."""

    __tablename__ = "public_publication_candidate_events"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "sequence",
            name="public_publication_candidate_event_sequence_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("public_publication_candidates.id", ondelete="CASCADE"),
        index=True,
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class KnowledgeIdempotencyRecord(Base):
    """Cross-job content-revision claim used to prevent duplicate processing."""

    __tablename__ = "knowledge_ingest_idempotency"

    idempotency_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_item_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(24))
    proposal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class KnowledgeJobEventRecord(Base):
    """Monotonic browser-visible progress event persisted before delivery."""

    __tablename__ = "knowledge_job_events"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="knowledge_event_job_sequence_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("knowledge_ingest_jobs.id", ondelete="CASCADE"), index=True
    )
    item_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("knowledge_ingest_items.id", ondelete="CASCADE"), nullable=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
