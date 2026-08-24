"""Cloud control-plane persistence tests."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from core.cloud.auth.models import CloudUser
from core.cloud.auth.repository import CloudRepository, RefreshTokenReuseDetected
from db.database import create_engine, create_session_factory
from db.migrations import init_db


@pytest.fixture
async def repository():
    """Provide an isolated database-backed cloud repository."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = create_session_factory(engine)
    await init_db(engine)
    try:
        yield CloudRepository(session_factory)
    finally:
        await engine.dispose()


@pytest.fixture
async def concurrent_repository(tmp_path: Path) -> AsyncIterator[CloudRepository]:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'cloud-concurrency.sqlite3'}")
    session_factory = create_session_factory(engine)
    await init_db(engine)
    try:
        yield CloudRepository(session_factory)
    finally:
        await engine.dispose()


async def test_identity_is_unique_and_invite_can_only_be_consumed_once(
    repository: CloudRepository,
) -> None:
    """One provider subject resolves to one invited cloud user."""
    await repository.create_invite("invite-one", email="owner@example.com")

    first = await repository.get_or_create_identity(
        provider="github",
        provider_subject="github-42",
        email="owner@example.com",
        display_name="Owner",
        invite_code="invite-one",
    )
    second = await repository.get_or_create_identity(
        provider="github",
        provider_subject="github-42",
        email="changed@example.com",
        display_name="Changed",
        invite_code=None,
    )

    assert first.user_id == second.user_id
    assert first.email == "owner@example.com"
    assert await repository.invite_is_consumed("invite-one") is True
    with pytest.raises(PermissionError, match="invite"):
        await repository.get_or_create_identity(
            provider="github",
            provider_subject="github-43",
            email="other@example.com",
            display_name="Other",
            invite_code="invite-one",
        )


async def test_login_session_stores_only_a_hash_and_honors_revoke_and_expiry(
    repository: CloudRepository,
) -> None:
    """Raw browser tokens never persist and invalid sessions do not authenticate."""
    await repository.create_invite("invite-two")
    user = await repository.get_or_create_identity(
        provider="github",
        provider_subject="github-99",
        email="user@example.com",
        display_name="User",
        invite_code="invite-two",
    )

    token = "browser-secret-token"
    session = await repository.create_session(
        user.user_id,
        token,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert session.token_hash != token
    assert await repository.raw_token_is_persisted(token) is False
    assert (await repository.authenticated_user(token)).user_id == user.user_id
    assert await repository.revoke_session(token) is True
    assert await repository.authenticated_user(token) is None

    await repository.create_session(
        user.user_id,
        "expired-token",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert await repository.authenticated_user("expired-token") is None


async def test_concurrent_refresh_rotation_revokes_the_winning_replacement(
    repository: CloudRepository,
) -> None:
    await repository.create_invite("refresh-race", email="refresh@example.com")
    user, login_session = await repository.create_canary_invite_session(
        invite_code="refresh-race",
        token="refresh-race-session",
        device_name="Concurrent client",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    original = "refresh-race-original-token-value"
    replacements = (
        "refresh-race-replacement-a-value",
        "refresh-race-replacement-b-value",
    )
    await repository.create_refresh_token(
        user_id=user.user_id,
        family_id=login_session.session_id,
        token=original,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )

    results = await asyncio.gather(
        *(
            repository.rotate_refresh_token(
                token=original,
                replacement=replacement,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
            for replacement in replacements
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, RefreshTokenReuseDetected) for result in results) == 1
    assert await repository.authenticated_user_by_session_id(login_session.session_id) is None
    winner_index = next(index for index, result in enumerate(results) if isinstance(result, tuple))
    winner = replacements[winner_index]
    loser = replacements[1 - winner_index]
    with pytest.raises(RefreshTokenReuseDetected):
        await repository.rotate_refresh_token(
            token=winner,
            replacement=f"{winner}-next",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    assert (
        await repository.rotate_refresh_token(
            token=loser,
            replacement=f"{loser}-next",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        is None
    )


async def test_revoking_one_device_frees_a_slot_without_affecting_other_devices(
    repository: CloudRepository,
) -> None:
    """Operators can remove one lost device while preserving the other sessions."""
    tokens: list[str] = []
    for index in range(1, 4):
        code = f"device-{index}"
        token = f"device-token-{index}"
        await repository.create_invite(code, email="owner@example.com")
        await repository.create_canary_invite_session(
            invite_code=code,
            token=token,
            device_name=f"Device {index}",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
        tokens.append(token)

    sessions = await repository.list_active_sessions("owner@example.com")
    revoked_id = next(item.session_id for item in sessions if item.device_name == "Device 2")

    assert await repository.revoke_device_session("other@example.com", revoked_id) is False
    assert await repository.revoke_device_session("owner@example.com", revoked_id) is True
    assert await repository.authenticated_user(tokens[0]) is not None
    assert await repository.authenticated_user(tokens[1]) is None
    assert await repository.authenticated_user(tokens[2]) is not None

    await repository.create_invite("replacement", email="owner@example.com")
    user, replacement = await repository.create_canary_invite_session(
        invite_code="replacement",
        token="replacement-token",
        device_name="Replacement",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    assert user.email == "owner@example.com"
    assert replacement.device_name == "Replacement"


async def test_disabling_an_account_revokes_every_device_and_blocks_new_invites(
    repository: CloudRepository,
) -> None:
    await repository.create_invite("disable-first", email="disabled@example.com")
    await repository.create_canary_invite_session(
        invite_code="disable-first",
        token="disabled-token",
        device_name="Phone",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )

    assert await repository.disable_user("disabled@example.com") is True
    assert await repository.authenticated_user("disabled-token") is None

    await repository.create_invite("disable-second", email="disabled@example.com")
    with pytest.raises(PermissionError, match="disabled"):
        await repository.create_canary_invite_session(
            invite_code="disable-second",
            token="new-token",
            device_name="New phone",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    assert await repository.invite_is_consumed("disable-second") is False


async def test_github_identity_reuses_existing_canary_account(
    repository: CloudRepository,
) -> None:
    first_invite = "canary-first-device"
    github_invite = "github-account-link"
    email = "owner@example.com"
    await repository.create_invite(first_invite, email=email)
    canary_user, _ = await repository.create_canary_invite_session(
        invite_code=first_invite,
        token="canary-token",
        device_name="iPhone Safari",
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    await repository.create_invite(github_invite, email=email)

    github_user = await repository.get_or_create_identity(
        provider="github",
        provider_subject="github-owner-123",
        email=email,
        display_name="Owner",
        invite_code=github_invite,
    )

    assert github_user.user_id == canary_user.user_id
    assert await repository.invite_is_consumed(github_invite) is True


async def test_canary_invite_creates_at_most_one_device_session_under_race(
    repository: CloudRepository,
) -> None:
    invite = "one-device-only"
    await repository.create_invite(invite, email="owner@example.com")

    results = await asyncio.gather(
        repository.create_canary_invite_session(
            invite_code=invite,
            token="first-device-token",
            device_name="First phone",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        repository.create_canary_invite_session(
            invite_code=invite,
            token="second-device-token",
            device_name="Second phone",
            expires_at=datetime.now(UTC) + timedelta(days=30),
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, PermissionError) for result in results) == 1
    assert len(await repository.list_active_sessions("owner@example.com")) == 1


async def test_canary_device_login_rolls_back_invite_session_and_identity_on_refresh_failure(
    repository: CloudRepository,
) -> None:
    expires_at = datetime.now(UTC) + timedelta(days=30)
    duplicate_refresh = "duplicate-refresh-token-value-for-atomic-canary"
    await repository.create_invite("refresh-seed", email="seed@example.com")
    seed_user, seed_session = await repository.create_canary_invite_session(
        invite_code="refresh-seed",
        token="refresh-seed-session",
        device_name="Seed",
        expires_at=expires_at,
    )
    await repository.create_refresh_token(
        user_id=seed_user.user_id,
        family_id=seed_session.session_id,
        token=duplicate_refresh,
        expires_at=expires_at,
    )
    await repository.create_invite("atomic-canary-failure", email="atomic@example.com")

    with pytest.raises(IntegrityError):
        await repository.create_canary_invite_session(
            invite_code="atomic-canary-failure",
            token="atomic-canary-session",
            device_name="Atomic Canary",
            expires_at=expires_at,
            refresh_token=duplicate_refresh,
            refresh_expires_at=expires_at,
        )

    assert await repository.invite_is_consumed("atomic-canary-failure") is False
    assert await repository.list_active_sessions("atomic@example.com") == []
    user, session = await repository.create_canary_invite_session(
        invite_code="atomic-canary-failure",
        token="atomic-canary-session-retry",
        device_name="Atomic Canary",
        expires_at=expires_at,
        refresh_token="fresh-canary-refresh-token-after-rollback",
        refresh_expires_at=expires_at,
    )
    assert user.email == "atomic@example.com"
    assert session.device_name == "Atomic Canary"


async def test_development_login_rolls_back_identity_and_invite_on_session_failure(
    repository: CloudRepository,
) -> None:
    expires_at = datetime.now(UTC) + timedelta(days=7)
    await repository.create_invite("development-seed", email="seed-dev@example.com")
    await repository.create_development_invite_session(
        email="seed-dev@example.com",
        display_name="Seed",
        invite_code="development-seed",
        token="duplicate-development-session-token",
        device_name="Seed browser",
        expires_at=expires_at,
    )
    await repository.create_invite("atomic-development-failure", email="dev@example.com")

    with pytest.raises(IntegrityError):
        await repository.create_development_invite_session(
            email="dev@example.com",
            display_name="Developer",
            invite_code="atomic-development-failure",
            token="duplicate-development-session-token",
            device_name="Browser",
            expires_at=expires_at,
        )

    assert await repository.invite_is_consumed("atomic-development-failure") is False
    assert await repository.list_active_sessions("dev@example.com") == []
    user, _ = await repository.create_development_invite_session(
        email="dev@example.com",
        display_name="Developer",
        invite_code="atomic-development-failure",
        token="development-session-after-rollback",
        device_name="Browser",
        expires_at=expires_at,
    )
    assert user.email == "dev@example.com"


async def test_development_login_reuses_an_existing_user_without_development_identity(
    repository: CloudRepository,
) -> None:
    email = "linked-dev@example.com"
    await repository.create_invite("linked-github", email=email)
    existing = await repository.get_or_create_identity(
        provider="github",
        provider_subject="linked-github-subject",
        email=email,
        display_name="Linked User",
        invite_code="linked-github",
    )
    await repository.create_invite("linked-development", email=email)

    user, session = await repository.create_development_invite_session(
        email=email,
        display_name="Linked User",
        invite_code="linked-development",
        token="linked-development-session",
        device_name="Development browser",
        expires_at=datetime.now(UTC) + timedelta(days=7),
        refresh_token="linked-development-refresh-token",
        refresh_expires_at=datetime.now(UTC) + timedelta(days=7),
    )

    assert user.user_id == existing.user_id
    assert session.user_id == existing.user_id
    assert await repository.invite_is_consumed("linked-development") is True


async def test_concurrent_canary_device_login_persists_one_complete_refresh_family(
    concurrent_repository: CloudRepository,
) -> None:
    repository = concurrent_repository
    expires_at = datetime.now(UTC) + timedelta(days=30)
    invite = "atomic-device-race"
    refresh_tokens = (
        "atomic-device-refresh-token-first-value",
        "atomic-device-refresh-token-second-value",
    )
    await repository.create_invite(invite, email="race@example.com")

    results = await asyncio.gather(
        *(
            repository.create_canary_invite_session(
                invite_code=invite,
                token=f"atomic-device-session-{index}",
                device_name=f"Device {index}",
                expires_at=expires_at,
                refresh_token=refresh_token,
                refresh_expires_at=expires_at,
            )
            for index, refresh_token in enumerate(refresh_tokens)
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, PermissionError) for result in results) == 1
    winner_index = next(index for index, result in enumerate(results) if isinstance(result, tuple))
    loser_index = 1 - winner_index
    assert (
        await repository.rotate_refresh_token(
            token=refresh_tokens[winner_index],
            replacement="winner-refresh-replacement-token-value",
            expires_at=expires_at,
        )
    ) is not None
    assert (
        await repository.rotate_refresh_token(
            token=refresh_tokens[loser_index],
            replacement="loser-refresh-replacement-token-value",
            expires_at=expires_at,
        )
        is None
    )


async def test_concurrent_development_login_persists_one_complete_refresh_family(
    concurrent_repository: CloudRepository,
) -> None:
    repository = concurrent_repository
    expires_at = datetime.now(UTC) + timedelta(days=7)
    invite = "atomic-development-race"
    email = "development-race@example.com"
    refresh_tokens = (
        "development-race-refresh-token-first-value",
        "development-race-refresh-token-second-value",
    )
    await repository.create_invite(invite, email=email)

    results = await asyncio.gather(
        *(
            repository.create_development_invite_session(
                email=email,
                display_name="Development Race",
                invite_code=invite,
                token=f"development-race-session-{index}",
                device_name=f"Browser {index}",
                expires_at=expires_at,
                refresh_token=refresh_token,
                refresh_expires_at=expires_at,
            )
            for index, refresh_token in enumerate(refresh_tokens)
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, PermissionError) for result in results) == 1
    assert len(await repository.list_active_sessions(email)) == 1
    winner_index = next(index for index, result in enumerate(results) if isinstance(result, tuple))
    loser_index = 1 - winner_index
    assert (
        await repository.rotate_refresh_token(
            token=refresh_tokens[winner_index],
            replacement="development-winner-refresh-replacement",
            expires_at=expires_at,
        )
    ) is not None
    assert (
        await repository.rotate_refresh_token(
            token=refresh_tokens[loser_index],
            replacement="development-loser-refresh-replacement",
            expires_at=expires_at,
        )
        is None
    )


async def test_workspace_lookup_is_scoped_to_its_project_owner(repository: CloudRepository) -> None:
    """Possessing an opaque workspace ID never grants cross-user access."""
    await repository.create_invite("invite-a")
    await repository.create_invite("invite-b")
    user_a = await repository.get_or_create_identity(
        provider="github",
        provider_subject="github-a",
        email="a@example.com",
        display_name="A",
        invite_code="invite-a",
    )
    user_b = await repository.get_or_create_identity(
        provider="github",
        provider_subject="github-b",
        email="b@example.com",
        display_name="B",
        invite_code="invite-b",
    )
    project = await repository.create_project(user_a.user_id, "A 的项目")
    workspace = await repository.create_workspace(project.project_id, provider="cloud")

    assert (
        await repository.authenticated_workspace(user_a.user_id, workspace.workspace_id)
    ).workspace_id == workspace.workspace_id
    assert await repository.authenticated_workspace(user_b.user_id, workspace.workspace_id) is None


async def test_one_time_invite_is_consumed_atomically(repository: CloudRepository) -> None:
    """Two concurrent registrations must never both turn one invite into users."""
    await repository.create_invite("atomic-invite")

    results = await asyncio.gather(
        repository.get_or_create_identity(
            provider="development",
            provider_subject="first@example.com",
            email="first@example.com",
            display_name="First",
            invite_code="atomic-invite",
        ),
        repository.get_or_create_identity(
            provider="development",
            provider_subject="second@example.com",
            email="second@example.com",
            display_name="Second",
            invite_code="atomic-invite",
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, CloudUser) for result in results) == 1
    assert sum(isinstance(result, PermissionError) for result in results) == 1
