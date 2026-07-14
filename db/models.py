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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class AuthIdentityRecord(Base):
    """A provider subject mapped to one Sage user."""

    __tablename__ = "cloud_auth_identities"
    __table_args__ = (UniqueConstraint("provider", "provider_subject", name="cloud_identity_provider_subject_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud_users.id", ondelete="CASCADE"), index=True)
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
    consumed_by_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("cloud_users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CloudLoginSessionRecord(Base):
    """Server-side session record; the browser token itself is never persisted."""

    __tablename__ = "cloud_login_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud_users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    owner_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud_users.id", ondelete="RESTRICT"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class CloudWorkspaceRecord(Base):
    """Opaque workspace metadata; no server filesystem path is stored here."""

    __tablename__ = "cloud_workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("cloud_projects.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))
    lifecycle_state: Mapped[str] = mapped_column(String(32), default="provisioning")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
