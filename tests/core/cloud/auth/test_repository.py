"""Cloud control-plane persistence tests."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from core.cloud.auth.models import CloudUser
from core.cloud.auth.repository import CloudRepository
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


async def test_identity_is_unique_and_invite_can_only_be_consumed_once(repository: CloudRepository) -> None:
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


async def test_workspace_lookup_is_scoped_to_its_project_owner(repository: CloudRepository) -> None:
    """Possessing an opaque workspace ID never grants cross-user access."""
    await repository.create_invite("invite-a")
    await repository.create_invite("invite-b")
    user_a = await repository.get_or_create_identity(
        provider="github", provider_subject="github-a", email="a@example.com",
        display_name="A", invite_code="invite-a",
    )
    user_b = await repository.get_or_create_identity(
        provider="github", provider_subject="github-b", email="b@example.com",
        display_name="B", invite_code="invite-b",
    )
    project = await repository.create_project(user_a.user_id, "A 的项目")
    workspace = await repository.create_workspace(project.project_id, provider="cloud")

    assert (await repository.authenticated_workspace(user_a.user_id, workspace.workspace_id)).workspace_id == workspace.workspace_id
    assert await repository.authenticated_workspace(user_b.user_id, workspace.workspace_id) is None


async def test_one_time_invite_is_consumed_atomically(repository: CloudRepository) -> None:
    """Two concurrent registrations must never both turn one invite into users."""
    await repository.create_invite("atomic-invite")

    results = await asyncio.gather(
        repository.get_or_create_identity(
            provider="development", provider_subject="first@example.com", email="first@example.com",
            display_name="First", invite_code="atomic-invite",
        ),
        repository.get_or_create_identity(
            provider="development", provider_subject="second@example.com", email="second@example.com",
            display_name="Second", invite_code="atomic-invite",
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, CloudUser) for result in results) == 1
    assert sum(isinstance(result, PermissionError) for result in results) == 1
