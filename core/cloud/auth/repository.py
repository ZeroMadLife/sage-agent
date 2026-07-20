"""Transactional repository for V7 identity and workspace ownership."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.cloud.auth.models import CloudLoginSession, CloudProject, CloudUser, CloudWorkspace
from db.models import (
    AuthIdentityRecord,
    CloudInviteRecord,
    CloudLoginSessionRecord,
    CloudOAuthTransactionRecord,
    CloudProjectRecord,
    CloudProviderCredentialRecord,
    CloudUserRecord,
    CloudWorkspaceRecord,
)


class DeviceLimitReached(Exception):
    """The account already has the maximum number of active device sessions."""


def _digest(value: str) -> str:
    """Derive a stable database lookup value without retaining a secret."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_expired(value: datetime | None, now: datetime) -> bool:
    if value is None:
        return False
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value <= now


async def _get_or_insert_user(
    session: AsyncSession,
    *,
    email: str,
    display_name: str,
) -> tuple[CloudUserRecord, bool]:
    """Resolve one email account, tolerating concurrent first-device registration."""
    existing = await session.scalar(
        select(CloudUserRecord)
        .where(CloudUserRecord.email == email)
        .with_for_update()
    )
    if existing is not None:
        return existing, False

    try:
        async with session.begin_nested():
            created = CloudUserRecord(
                id=str(uuid4()),
                email=email,
                display_name=display_name,
            )
            session.add(created)
            await session.flush()
        return created, True
    except IntegrityError:
        race_winner = await session.scalar(
            select(CloudUserRecord)
            .where(CloudUserRecord.email == email)
            .with_for_update()
        )
        if race_winner is None:
            raise
        return race_winner, False


class CloudRepository:
    """Keep cloud ownership queries out of HTTP routes and harness code."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_invite(self, code: str, *, email: str = "") -> None:
        """Create an invite stored only as a code digest."""
        if not code:
            raise ValueError("invite code is required")
        async with self._session_factory() as session:
            session.add(
                CloudInviteRecord(
                    id=str(uuid4()), code_hash=_digest(code), email=email.strip().lower() or None
                )
            )
            await session.commit()

    async def invite_is_consumed(self, code: str) -> bool:
        """Report whether an invite code has been consumed without exposing its row."""
        async with self._session_factory() as session:
            invite = await session.scalar(
                select(CloudInviteRecord).where(CloudInviteRecord.code_hash == _digest(code))
            )
            return invite is not None and invite.consumed_at is not None

    async def get_or_create_identity(
        self,
        *,
        provider: str,
        provider_subject: str,
        email: str,
        display_name: str,
        invite_code: str | None,
        reject_existing_identity: bool = False,
    ) -> CloudUser:
        """Resolve a provider identity, consuming one valid invite for new users."""
        if not provider or not provider_subject:
            raise ValueError("provider and provider subject are required")
        normalized_email = email.strip().lower()
        now = _utc_now()
        async with self._session_factory() as session:
            identity = await session.scalar(
                select(AuthIdentityRecord).where(
                    AuthIdentityRecord.provider == provider,
                    AuthIdentityRecord.provider_subject == provider_subject,
                )
            )
            if identity is not None:
                if reject_existing_identity:
                    raise PermissionError("development identity is already bootstrapped")
                user = await session.get(CloudUserRecord, identity.user_id)
                if user is None:
                    raise RuntimeError("identity has no user")
                return _to_user(user)

            if not invite_code:
                raise PermissionError("an unused invite is required")
            user, created = await _get_or_insert_user(
                session,
                email=normalized_email,
                display_name=display_name.strip(),
            )
            if user is not None and user.disabled_at is not None:
                raise PermissionError("cloud user is disabled")
            if not created and reject_existing_identity:
                raise PermissionError("development identity is already bootstrapped")
            # A conditional update is the one-time-invite lock. Checking then
            # updating would allow two concurrent registrations to consume it.
            consumed = await session.execute(
                update(CloudInviteRecord)
                .where(
                    CloudInviteRecord.code_hash == _digest(invite_code),
                    CloudInviteRecord.consumed_at.is_(None),
                    (CloudInviteRecord.expires_at.is_(None))
                    | (CloudInviteRecord.expires_at > func.now()),
                    (CloudInviteRecord.email.is_(None))
                    | (CloudInviteRecord.email == normalized_email),
                )
                .values(consumed_at=now, consumed_by_user_id=user.id)
            )
            if consumed.rowcount != 1:
                await session.rollback()
                raise PermissionError("invite is invalid or already consumed")
            session.add(
                AuthIdentityRecord(
                    id=str(uuid4()), user_id=user.id, provider=provider,
                    provider_subject=provider_subject,
                )
            )
            await session.commit()
            return _to_user(user)

    async def create_session(
        self,
        user_id: str,
        token: str,
        *,
        expires_at: datetime,
        device_name: str = "Unknown device",
        max_active_sessions: int = 3,
    ) -> CloudLoginSession:
        """Persist a session digest; raw token remains exclusively in the cookie."""
        if not token:
            raise ValueError("session token is required")
        token_hash = _digest(token)
        async with self._session_factory() as session:
            user = await session.scalar(
                select(CloudUserRecord)
                .where(CloudUserRecord.id == user_id)
                .with_for_update()
            )
            if user is None or user.disabled_at is not None:
                raise PermissionError("cloud user is unavailable")
            active_sessions = await session.scalar(
                select(func.count(CloudLoginSessionRecord.id)).where(
                    CloudLoginSessionRecord.user_id == user_id,
                    CloudLoginSessionRecord.revoked_at.is_(None),
                    CloudLoginSessionRecord.expires_at > func.now(),
                )
            )
            if int(active_sessions or 0) >= max_active_sessions:
                raise DeviceLimitReached
            now = _utc_now()
            record = CloudLoginSessionRecord(
                id=str(uuid4()),
                user_id=user_id,
                token_hash=token_hash,
                device_name=_normalize_device_name(device_name),
                expires_at=expires_at,
                last_seen_at=now,
            )
            session.add(record)
            await session.commit()
            return _to_login_session(record)

    async def create_canary_invite_session(
        self,
        *,
        invite_code: str,
        token: str,
        device_name: str,
        expires_at: datetime,
        max_active_sessions: int = 3,
    ) -> tuple[CloudUser, CloudLoginSession]:
        """Atomically consume one email-bound invite and create one device session."""
        if not invite_code or not token:
            raise ValueError("invite code and session token are required")
        now = _utc_now()
        async with self._session_factory() as session:
            invite = await session.scalar(
                select(CloudInviteRecord)
                .where(CloudInviteRecord.code_hash == _digest(invite_code))
                .with_for_update()
            )
            if (
                invite is None
                or invite.consumed_at is not None
                or _is_expired(invite.expires_at, now)
                or not invite.email
            ):
                raise PermissionError("invite is invalid or already consumed")

            email = invite.email.strip().lower()
            # Claim the invite before creating a first-time account. Keeping the
            # user reference unset until after flush avoids PostgreSQL's immediate
            # foreign-key check while preserving one atomic rollback boundary.
            consumed = await session.execute(
                update(CloudInviteRecord)
                .where(
                    CloudInviteRecord.id == invite.id,
                    CloudInviteRecord.consumed_at.is_(None),
                    (CloudInviteRecord.expires_at.is_(None))
                    | (CloudInviteRecord.expires_at > func.now()),
                )
                .values(consumed_at=now)
            )
            if consumed.rowcount != 1:
                await session.rollback()
                raise PermissionError("invite is invalid or already consumed")

            user, _ = await _get_or_insert_user(
                session,
                email=email,
                display_name=email.partition("@")[0],
            )
            if user.disabled_at is not None:
                raise PermissionError("cloud user is disabled")

            active_sessions = await session.scalar(
                select(func.count(CloudLoginSessionRecord.id)).where(
                    CloudLoginSessionRecord.user_id == user.id,
                    CloudLoginSessionRecord.revoked_at.is_(None),
                    CloudLoginSessionRecord.expires_at > func.now(),
                )
            )
            if int(active_sessions or 0) >= max_active_sessions:
                await session.rollback()
                raise DeviceLimitReached
            invite.consumed_by_user_id = user.id

            identity = await session.scalar(
                select(AuthIdentityRecord).where(
                    AuthIdentityRecord.provider == "canary_invite",
                    AuthIdentityRecord.provider_subject == email,
                )
            )
            if identity is None:
                session.add(
                    AuthIdentityRecord(
                        id=str(uuid4()),
                        user_id=user.id,
                        provider="canary_invite",
                        provider_subject=email,
                    )
                )

            record = CloudLoginSessionRecord(
                id=str(uuid4()),
                user_id=user.id,
                token_hash=_digest(token),
                device_name=_normalize_device_name(device_name),
                expires_at=expires_at,
                last_seen_at=now,
            )
            session.add(record)
            await session.commit()
            return _to_user(user), _to_login_session(record)

    async def authenticated_user(self, token: str) -> CloudUser | None:
        """Resolve a non-expired, non-revoked browser session token."""
        if not token:
            return None
        now = _utc_now()
        async with self._session_factory() as session:
            record = await session.scalar(
                select(CloudLoginSessionRecord).where(
                    CloudLoginSessionRecord.token_hash == _digest(token)
                )
            )
            if record is None or record.revoked_at is not None or _is_expired(record.expires_at, now):
                return None
            user = await session.get(CloudUserRecord, record.user_id)
            if user is None or user.disabled_at is not None:
                return None
            if record.last_seen_at is None or (now - _as_utc(record.last_seen_at)).total_seconds() >= 900:
                record.last_seen_at = now
                await session.commit()
            return _to_user(user)

    async def revoke_session(self, token: str) -> bool:
        """Revoke one browser session by token digest."""
        async with self._session_factory() as session:
            record = await session.scalar(
                select(CloudLoginSessionRecord).where(
                    CloudLoginSessionRecord.token_hash == _digest(token)
                )
            )
            if record is None or record.revoked_at is not None:
                return False
            record.revoked_at = _utc_now()
            await session.commit()
            return True

    async def raw_token_is_persisted(self, token: str) -> bool:
        """Test-only invariant probe: stored digest columns never equal the raw token."""
        async with self._session_factory() as session:
            values = (await session.scalars(select(CloudLoginSessionRecord.token_hash))).all()
            return token in values

    async def list_active_sessions(self, email: str) -> list[CloudLoginSession]:
        """List active device sessions for one operator-selected account."""
        normalized_email = email.strip().lower()
        async with self._session_factory() as session:
            user_id = await session.scalar(
                select(CloudUserRecord.id).where(CloudUserRecord.email == normalized_email)
            )
            if user_id is None:
                return []
            records = (
                await session.scalars(
                    select(CloudLoginSessionRecord)
                    .where(
                        CloudLoginSessionRecord.user_id == user_id,
                        CloudLoginSessionRecord.revoked_at.is_(None),
                        CloudLoginSessionRecord.expires_at > func.now(),
                    )
                    .order_by(CloudLoginSessionRecord.created_at.desc())
                )
            ).all()
            return [_to_login_session(record) for record in records]

    async def revoke_device_session(self, email: str, session_id: str) -> bool:
        """Revoke one device only when it belongs to the selected account."""
        normalized_email = email.strip().lower()
        async with self._session_factory() as session:
            record = await session.scalar(
                select(CloudLoginSessionRecord)
                .join(CloudUserRecord, CloudLoginSessionRecord.user_id == CloudUserRecord.id)
                .where(
                    CloudUserRecord.email == normalized_email,
                    CloudLoginSessionRecord.id == session_id,
                    CloudLoginSessionRecord.revoked_at.is_(None),
                )
            )
            if record is None:
                return False
            record.revoked_at = _utc_now()
            await session.commit()
            return True

    async def disable_user(self, email: str) -> bool:
        """Disable one account and revoke every active device session."""
        normalized_email = email.strip().lower()
        now = _utc_now()
        async with self._session_factory() as session:
            user = await session.scalar(
                select(CloudUserRecord)
                .where(CloudUserRecord.email == normalized_email)
                .with_for_update()
            )
            if user is None:
                return False
            user.disabled_at = now
            await session.execute(
                update(CloudLoginSessionRecord)
                .where(
                    CloudLoginSessionRecord.user_id == user.id,
                    CloudLoginSessionRecord.revoked_at.is_(None),
                )
                .values(revoked_at=now)
            )
            await session.commit()
            return True

    async def create_oauth_transaction(
        self,
        *,
        provider: str,
        state: str,
        browser_binding: str,
        encrypted_payload: str,
        expires_at: datetime,
    ) -> None:
        """Persist only a state digest and an authenticated encrypted payload."""
        if not state or not encrypted_payload:
            raise ValueError("OAuth transaction state and payload are required")
        async with self._session_factory() as session:
            session.add(
                CloudOAuthTransactionRecord(
                    id=str(uuid4()),
                    provider=provider,
                    state_hash=_digest(state),
                    browser_binding_hash=_digest(browser_binding),
                    encrypted_payload=encrypted_payload,
                    expires_at=expires_at,
                )
            )
            await session.commit()

    async def consume_oauth_transaction(
        self, *, provider: str, state: str, browser_binding: str
    ) -> str:
        """Atomically consume one non-expired transaction and return its ciphertext."""
        now = _utc_now()
        async with self._session_factory() as session:
            record = await session.scalar(
                select(CloudOAuthTransactionRecord).where(
                    CloudOAuthTransactionRecord.provider == provider,
                    CloudOAuthTransactionRecord.state_hash == _digest(state),
                    CloudOAuthTransactionRecord.browser_binding_hash
                    == _digest(browser_binding),
                )
            )
            if record is None:
                raise PermissionError("OAuth transaction is invalid")
            consumed = await session.execute(
                update(CloudOAuthTransactionRecord)
                .where(
                    CloudOAuthTransactionRecord.id == record.id,
                    CloudOAuthTransactionRecord.consumed_at.is_(None),
                    CloudOAuthTransactionRecord.expires_at > func.now(),
                )
                .values(consumed_at=now)
            )
            if consumed.rowcount != 1:
                await session.rollback()
                raise PermissionError("OAuth transaction is expired or already consumed")
            encrypted_payload = record.encrypted_payload
            await session.commit()
            return encrypted_payload

    async def upsert_provider_credential(
        self,
        *,
        user_id: str,
        provider: str,
        encrypted_access_token: str,
        scopes: str,
        provider_login: str,
    ) -> None:
        """Store one encrypted credential per user and provider."""
        async with self._session_factory() as session:
            record = await session.scalar(
                select(CloudProviderCredentialRecord).where(
                    CloudProviderCredentialRecord.user_id == user_id,
                    CloudProviderCredentialRecord.provider == provider,
                )
            )
            if record is None:
                session.add(
                    CloudProviderCredentialRecord(
                        id=str(uuid4()),
                        user_id=user_id,
                        provider=provider,
                        encrypted_access_token=encrypted_access_token,
                        scopes=scopes,
                        provider_login=provider_login,
                    )
                )
            else:
                record.encrypted_access_token = encrypted_access_token
                record.scopes = scopes
                record.provider_login = provider_login
            await session.commit()

    async def raw_provider_token_is_persisted(self, token: str) -> bool:
        """Test-only invariant probe for encrypted provider credentials."""
        async with self._session_factory() as session:
            values = (
                await session.scalars(
                    select(CloudProviderCredentialRecord.encrypted_access_token)
                )
            ).all()
            return token in values

    async def create_project(self, owner_user_id: str, name: str) -> CloudProject:
        """Create a project whose owner defines the initial access boundary."""
        if not name.strip():
            raise ValueError("project name is required")
        async with self._session_factory() as session:
            record = CloudProjectRecord(
                id=str(uuid4()), owner_user_id=owner_user_id, name=name.strip()
            )
            session.add(record)
            await session.commit()
            return _to_project(record)

    async def create_workspace(self, project_id: str, *, provider: str) -> CloudWorkspace:
        """Create metadata only; V7.0 never creates a checkout or runner."""
        if provider not in {"cloud", "local_companion"}:
            raise ValueError("unknown workspace provider")
        async with self._session_factory() as session:
            record = CloudWorkspaceRecord(
                id=str(uuid4()), project_id=project_id, provider=provider,
                lifecycle_state="provisioning",
            )
            session.add(record)
            await session.commit()
            return _to_workspace(record)

    async def authenticated_project(self, user_id: str, project_id: str) -> CloudProject | None:
        """Resolve one project only when it is owned by the authenticated user."""
        async with self._session_factory() as session:
            record = await session.scalar(
                select(CloudProjectRecord).where(
                    CloudProjectRecord.id == project_id,
                    CloudProjectRecord.owner_user_id == user_id,
                )
            )
            return _to_project(record) if record is not None else None

    async def list_projects(self, user_id: str) -> list[CloudProject]:
        """Return only the caller's projects in stable newest-first order."""
        async with self._session_factory() as session:
            records = (
                await session.scalars(
                    select(CloudProjectRecord)
                    .where(CloudProjectRecord.owner_user_id == user_id)
                    .order_by(CloudProjectRecord.created_at.desc(), CloudProjectRecord.id.desc())
                )
            ).all()
            return [_to_project(record) for record in records]

    async def authenticated_workspace(
        self, user_id: str, workspace_id: str
    ) -> CloudWorkspace | None:
        """Resolve exactly one owner-visible workspace by opaque ID."""
        async with self._session_factory() as session:
            record = await session.scalar(
                select(CloudWorkspaceRecord)
                .join(CloudProjectRecord, CloudWorkspaceRecord.project_id == CloudProjectRecord.id)
                .where(
                    CloudWorkspaceRecord.id == workspace_id,
                    CloudProjectRecord.owner_user_id == user_id,
                )
            )
            return _to_workspace(record) if record is not None else None


def new_browser_session_token() -> str:
    """Generate the opaque random value that is later sent only in a cookie."""
    return secrets.token_urlsafe(32)


def _to_user(record: CloudUserRecord) -> CloudUser:
    return CloudUser(user_id=record.id, email=record.email, display_name=record.display_name)


def _to_login_session(record: CloudLoginSessionRecord) -> CloudLoginSession:
    return CloudLoginSession(
        session_id=record.id, user_id=record.user_id, token_hash=record.token_hash,
        device_name=record.device_name,
        expires_at=record.expires_at,
        last_seen_at=record.last_seen_at,
    )


def _normalize_device_name(value: str) -> str:
    normalized = " ".join(value.split())[:120]
    return normalized or "Unknown device"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _to_project(record: CloudProjectRecord) -> CloudProject:
    return CloudProject(project_id=record.id, owner_user_id=record.owner_user_id, name=record.name)


def _to_workspace(record: CloudWorkspaceRecord) -> CloudWorkspace:
    return CloudWorkspace(
        workspace_id=record.id, project_id=record.project_id, provider=record.provider,
        lifecycle_state=record.lifecycle_state,
    )
