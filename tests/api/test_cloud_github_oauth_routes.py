"""GitHub OAuth security boundary tests for the V7 cloud control plane."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from core.cloud.auth.repository import CloudRepository
from core.cloud.github import GitHubOAuthConfig, GitHubOAuthService
from db.database import create_engine, create_session_factory
from db.migrations import init_db

_TRANSACTION_SECRET = "transaction-secret-that-is-at-least-32-characters"
_TOKEN_SECRET = "token-encryption-secret-that-is-at-least-32-characters"
_ACCESS_TOKEN = "github-access-token-must-never-leak"


@pytest.fixture
async def cloud_repository() -> AsyncIterator[CloudRepository]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    session_factory = create_session_factory(engine)
    await init_db(engine)
    try:
        yield CloudRepository(session_factory)
    finally:
        await engine.dispose()


def _oauth_service(
    repository: CloudRepository,
    handler: httpx.MockTransport,
) -> tuple[GitHubOAuthService, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=handler)
    service = GitHubOAuthService(
        repository,
        GitHubOAuthConfig(
            client_id="github-client-id",
            client_secret="github-client-secret",
            redirect_uri="http://localhost:8000/api/v1/cloud/auth/github/callback",
            scope="read:user user:email",
            transaction_secret=_TRANSACTION_SECRET,
            token_encryption_secret=_TOKEN_SECRET,
        ),
        http_client=client,
    )
    return service, client


def _successful_github(
    token_requests: list[httpx.Request],
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            token_requests.append(request)
            return httpx.Response(
                200,
                json={"access_token": _ACCESS_TOKEN, "scope": "read:user,user:email"},
            )
        if request.url.path == "/user/emails":
            return httpx.Response(
                200,
                json=[
                    {
                        "email": "developer@example.com",
                        "primary": True,
                        "verified": True,
                    }
                ],
            )
        if request.url.path == "/user":
            return httpx.Response(
                200,
                json={"id": 123456, "login": "developer", "name": "Developer"},
            )
        raise AssertionError(f"unexpected GitHub request: {request.url}")

    return httpx.MockTransport(handler)


async def test_github_oauth_uses_pkce_browser_binding_and_encrypted_token(
    cloud_repository: CloudRepository,
) -> None:
    """A successful callback never exposes or persists provider token plaintext."""
    await cloud_repository.create_invite("invite-secret", email="developer@example.com")
    token_requests: list[httpx.Request] = []
    service, github_client = _oauth_service(cloud_repository, _successful_github(token_requests))
    client = TestClient(
        create_app(
            cloud_repository=cloud_repository,
            cloud_github_oauth_service=service,
            cloud_frontend_url="http://localhost:5173",
            cloud_app_env="development",
        )
    )

    started = client.post(
        "/api/v1/cloud/auth/github/start",
        json={"invite_code": "invite-secret", "return_to": "/#/coding"},
    )
    authorization_url = started.json()["authorization_url"]
    query = parse_qs(urlparse(authorization_url).query)
    completed = client.get(
        "/api/v1/cloud/auth/github/callback",
        params={"code": "one-time-code", "state": query["state"][0]},
        follow_redirects=False,
    )
    current = client.get("/api/v1/cloud/me")

    await github_client.aclose()
    assert started.status_code == 200
    assert started.headers["cache-control"] == "no-store"
    assert "HttpOnly" in started.headers["set-cookie"]
    assert "invite-secret" not in authorization_url
    assert "code_verifier" not in authorization_url
    assert query["code_challenge_method"] == ["S256"]
    assert query["scope"] == ["read:user user:email"]
    assert completed.status_code == 303
    assert completed.headers["location"] == "http://localhost:5173/#/coding"
    assert "HttpOnly" in completed.headers["set-cookie"]
    assert current.status_code == 200
    assert current.json()["email"] == "developer@example.com"
    assert _ACCESS_TOKEN not in started.text
    assert _ACCESS_TOKEN not in completed.text
    assert await cloud_repository.raw_provider_token_is_persisted(_ACCESS_TOKEN) is False

    token_request = token_requests[0]
    token_form = parse_qs(token_request.content.decode("utf-8"))
    verifier = token_form["code_verifier"][0]
    expected_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    assert 43 <= len(verifier) <= 128
    assert query["code_challenge"] == [expected_challenge]


async def test_github_callback_rejects_wrong_browser_before_token_exchange(
    cloud_repository: CloudRepository,
) -> None:
    """A stolen state is useless without the HttpOnly browser binding."""
    outbound: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        outbound.append(request)
        return httpx.Response(500)

    service, github_client = _oauth_service(cloud_repository, httpx.MockTransport(handler))
    app = create_app(
        cloud_repository=cloud_repository,
        cloud_github_oauth_service=service,
        cloud_app_env="development",
    )
    owner_browser = TestClient(app)
    other_browser = TestClient(app)
    started = owner_browser.post("/api/v1/cloud/auth/github/start", json={"return_to": "/#/coding"})
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]

    completed = other_browser.get(
        "/api/v1/cloud/auth/github/callback",
        params={"code": "stolen-code", "state": state},
        follow_redirects=False,
    )

    await github_client.aclose()
    assert completed.status_code == 400
    assert outbound == []


async def test_github_callback_state_is_single_use(
    cloud_repository: CloudRepository,
) -> None:
    """A callback replay cannot exchange a second token or create another session."""
    await cloud_repository.create_invite("replay-invite", email="developer@example.com")
    token_requests: list[httpx.Request] = []
    service, github_client = _oauth_service(cloud_repository, _successful_github(token_requests))
    client = TestClient(
        create_app(
            cloud_repository=cloud_repository,
            cloud_github_oauth_service=service,
            cloud_app_env="development",
        )
    )
    started = client.post(
        "/api/v1/cloud/auth/github/start",
        json={"invite_code": "replay-invite", "return_to": "/#/coding"},
    )
    state = parse_qs(urlparse(started.json()["authorization_url"]).query)["state"][0]
    binding = client.cookies.get("sage_oauth_binding")

    first = client.get(
        "/api/v1/cloud/auth/github/callback",
        params={"code": "first-code", "state": state},
        follow_redirects=False,
    )
    client.cookies.set(
        "sage_oauth_binding",
        binding,
        path="/api/v1/cloud/auth/github/callback",
    )
    replay = client.get(
        "/api/v1/cloud/auth/github/callback",
        params={"code": "second-code", "state": state},
        follow_redirects=False,
    )

    await github_client.aclose()
    assert first.status_code == 303
    assert replay.status_code == 400
    assert len(token_requests) == 1


def test_github_oauth_start_is_unavailable_without_server_config(
    cloud_repository: CloudRepository,
) -> None:
    client = TestClient(create_app(cloud_repository=cloud_repository))

    response = client.post("/api/v1/cloud/auth/github/start", json={"return_to": "/#/coding"})

    assert response.status_code == 503


def test_github_oauth_rejects_open_redirect(
    cloud_repository: CloudRepository,
) -> None:
    client = TestClient(create_app(cloud_repository=cloud_repository))

    response = client.post(
        "/api/v1/cloud/auth/github/start",
        json={"return_to": "https://attacker.example/steal"},
    )

    assert response.status_code == 422
