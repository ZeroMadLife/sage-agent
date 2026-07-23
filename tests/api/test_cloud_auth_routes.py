"""Cloud authentication boundary tests."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from core.cloud.auth.repository import CloudRepository
from db.database import create_engine, create_session_factory
from db.migrations import init_db


@pytest.fixture
async def cloud_repository() -> AsyncIterator[CloudRepository]:
    """Use a fresh control-plane database for each HTTP test."""
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = create_session_factory(engine)
    await init_db(engine)
    try:
        yield CloudRepository(session_factory)
    finally:
        await engine.dispose()


def test_cloud_me_requires_a_server_session(cloud_repository: CloudRepository) -> None:
    """The cloud API never trusts a browser-provided user ID."""
    client = TestClient(
        create_app(
            cloud_repository=cloud_repository,
            cloud_dev_login_enabled=True,
            cloud_app_env="development",
        )
    )

    response = client.get("/api/v1/cloud/me")

    assert response.status_code == 401


async def test_development_login_sets_httponly_cookie_and_me_reads_server_session(
    cloud_repository: CloudRepository,
) -> None:
    """The token is a cookie-only capability and is not returned in JSON."""
    await cloud_repository.create_invite("dev-invite", email="dev@example.com")
    client = TestClient(
        create_app(
            cloud_repository=cloud_repository,
            cloud_dev_login_enabled=True,
            cloud_app_env="development",
        )
    )

    login = client.post(
        "/api/v1/cloud/auth/dev/login",
        json={"email": "dev@example.com", "display_name": "Dev", "invite_code": "dev-invite"},
    )
    current = client.get("/api/v1/cloud/me")

    assert login.status_code == 200
    assert "token" not in login.json()
    cookie = login.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert current.status_code == 200
    assert current.json() == {
        "user_id": login.json()["user_id"],
        "email": "dev@example.com",
        "display_name": "Dev",
    }


async def test_canary_invite_login_sets_a_30_day_secure_device_session(
    cloud_repository: CloudRepository,
) -> None:
    """Private Canary users can enter with one invite and no GitHub round trip."""
    await cloud_repository.create_invite("phone-invite", email="owner@example.com")
    client = TestClient(
        create_app(
            cloud_repository=cloud_repository,
            cloud_canary_invite_login_enabled=True,
            cloud_app_env="production",
        ),
        base_url="https://testserver",
    )

    login = client.post(
        "/api/v1/cloud/auth/canary/login",
        json={"invite_code": "phone-invite", "device_name": "iPhone Safari"},
    )
    current = client.get("/api/v1/cloud/me")

    assert login.status_code == 200
    assert login.json()["email"] == "owner@example.com"
    assert current.status_code == 200
    cookie = login.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=lax" in cookie
    assert "Max-Age=2592000" in cookie


async def test_canary_invite_login_is_hidden_unless_explicitly_enabled(
    cloud_repository: CloudRepository,
) -> None:
    await cloud_repository.create_invite("hidden-invite", email="owner@example.com")
    client = TestClient(
        create_app(
            cloud_repository=cloud_repository,
            cloud_canary_invite_login_enabled=False,
            cloud_app_env="production",
        )
    )

    response = client.post(
        "/api/v1/cloud/auth/canary/login",
        json={"invite_code": "hidden-invite", "device_name": "iPhone Safari"},
    )

    assert response.status_code == 404
    assert await cloud_repository.invite_is_consumed("hidden-invite") is False


async def test_canary_invite_login_rejects_a_fourth_device_without_consuming_invite(
    cloud_repository: CloudRepository,
) -> None:
    client = TestClient(
        create_app(
            cloud_repository=cloud_repository,
            cloud_canary_invite_login_enabled=True,
            cloud_app_env="production",
        )
    )
    for index in range(1, 5):
        await cloud_repository.create_invite(f"device-invite-{index}", email="owner@example.com")
        client.cookies.clear()
        response = client.post(
            "/api/v1/cloud/auth/canary/login",
            json={
                "invite_code": f"device-invite-{index}",
                "device_name": f"Device {index}",
            },
        )
        if index < 4:
            assert response.status_code == 200
        else:
            assert response.status_code == 409
            assert response.json()["detail"] == "最多允许 3 台设备保持登录"

    assert await cloud_repository.invite_is_consumed("device-invite-4") is False


async def test_logout_revokes_server_session_and_removes_cookie(
    cloud_repository: CloudRepository,
) -> None:
    """Logging out invalidates the current token even if the browser retains it."""
    await cloud_repository.create_invite("logout-invite")
    client = TestClient(
        create_app(
            cloud_repository=cloud_repository,
            cloud_dev_login_enabled=True,
            cloud_app_env="development",
        )
    )
    login = client.post(
        "/api/v1/cloud/auth/dev/login",
        json={
            "email": "logout@example.com",
            "display_name": "Logout",
            "invite_code": "logout-invite",
        },
    )
    retained_token = client.cookies.get("sage_session")

    logout = client.post("/api/v1/cloud/auth/logout")
    client.cookies.set("sage_session", retained_token)
    current = client.get("/api/v1/cloud/me")

    assert login.status_code == 200
    assert logout.status_code == 204
    assert "sage_session=" in logout.headers["set-cookie"]
    assert current.status_code == 401


async def test_development_login_cannot_impersonate_an_existing_identity(
    cloud_repository: CloudRepository,
) -> None:
    """Dev bootstrap is one-time, so an email is never a repeat-login credential."""
    await cloud_repository.create_invite("first-invite", email="dev@example.com")
    await cloud_repository.create_invite("second-invite", email="dev@example.com")
    client = TestClient(
        create_app(
            cloud_repository=cloud_repository,
            cloud_dev_login_enabled=True,
            cloud_app_env="development",
        )
    )

    first_login = client.post(
        "/api/v1/cloud/auth/dev/login",
        json={"email": "dev@example.com", "display_name": "Dev", "invite_code": "first-invite"},
    )
    client.cookies.clear()
    impersonation = client.post(
        "/api/v1/cloud/auth/dev/login",
        json={
            "email": "dev@example.com",
            "display_name": "Attacker",
            "invite_code": "second-invite",
        },
    )

    assert first_login.status_code == 200
    assert impersonation.status_code == 403
    assert await cloud_repository.invite_is_consumed("second-invite") is False


def test_development_login_route_is_hidden_when_disabled(cloud_repository: CloudRepository) -> None:
    """Production cannot accidentally expose a passwordless development login."""
    client = TestClient(
        create_app(cloud_repository=cloud_repository, cloud_dev_login_enabled=False)
    )

    response = client.post(
        "/api/v1/cloud/auth/dev/login",
        json={"email": "hidden@example.com", "display_name": "Hidden", "invite_code": "hidden"},
    )

    assert response.status_code == 404


async def test_development_login_is_hidden_outside_development(
    cloud_repository: CloudRepository,
) -> None:
    """An accidental feature flag cannot expose the local bootstrap in production."""
    client = TestClient(
        create_app(
            cloud_repository=cloud_repository,
            cloud_dev_login_enabled=True,
            cloud_app_env="production",
        )
    )

    response = client.post(
        "/api/v1/cloud/auth/dev/login",
        json={"email": "hidden@example.com", "display_name": "Hidden", "invite_code": "hidden"},
    )

    assert response.status_code == 404


async def test_production_session_cookie_is_secure_even_when_override_is_false(
    cloud_repository: CloudRepository,
) -> None:
    """HTTPS deployments cannot accidentally issue a non-Secure session cookie."""
    await cloud_repository.create_invite("production-invite", email="prod@example.com")
    app = create_app(
        cloud_repository=cloud_repository,
        cloud_app_env="production",
        cloud_secure_cookies=False,
    )
    # Bootstrap a test-only authenticated user without exposing the dev route.
    user = await cloud_repository.get_or_create_identity(
        provider="github",
        provider_subject="prod-subject",
        email="prod@example.com",
        display_name="Prod",
        invite_code="production-invite",
    )
    token = "production-cookie-token"
    await cloud_repository.create_session(
        user.user_id, token, expires_at=datetime.now(UTC) + timedelta(days=1)
    )
    client = TestClient(app)
    client.cookies.set("sage_session", token)

    response = client.post("/api/v1/cloud/auth/logout")

    assert response.status_code == 204
    assert "Secure" in response.headers["set-cookie"]


def test_cloud_payload_rejects_whitespace_values(cloud_repository: CloudRepository) -> None:
    """Whitespace-only user input is a validation error, never a route 500."""
    client = TestClient(create_app(cloud_repository=cloud_repository, cloud_dev_login_enabled=True))

    login = client.post(
        "/api/v1/cloud/auth/dev/login",
        json={"email": "   ", "display_name": "", "invite_code": "   "},
    )

    assert login.status_code == 422
