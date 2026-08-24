"""Production authentication boundary for Coding REST and WebSocket routes."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse
from starlette.websockets import WebSocketDisconnect

from api.main import create_app
from core.cloud.auth.repository import CloudRepository
from core.cloud.model_providers import ModelProviderRepository
from core.coding.persistence import CodingSessionStore
from db.database import create_engine, create_session_factory
from db.migrations import init_db


class FakeModel:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def complete(self, _prompt: str) -> str:
        return "<final>done</final>"


async def _production_client(tmp_path: Path) -> tuple[TestClient, CloudRepository, object]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    factory = create_session_factory(engine)
    await init_db(engine)
    repository = CloudRepository(factory)
    providers = ModelProviderRepository(factory, encryption_secret="test-secret-" * 4)
    app = create_app(
        cloud_repository=repository,
        cloud_model_provider_repository=providers,
        cloud_app_env="production",
        cloud_token_secret="test-only-jwt-signing-secret-that-is-long-enough",
        coding_model_factory=FakeModel,
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    return TestClient(app), repository, engine


def _save_unowned_session(client: TestClient, tmp_path: Path) -> None:
    CodingSessionStore(client.app.state.coding_storage_root / "sessions").save(
        {
            "id": "legacy-unowned",
            "workspace_root": str(tmp_path),
            "created_at": "2026-08-24T00:00:00+00:00",
            "updated_at": "2026-08-24T00:00:00+00:00",
            "history": [{"role": "user", "content": "private legacy content"}],
        }
    )


async def _device_bearer(client: TestClient, repository: CloudRepository) -> dict[str, str]:
    await repository.create_invite("legacy-reader", email="reader@example.com")
    login = client.post(
        "/api/v1/cloud/auth/device/login",
        json={"invite_code": "legacy-reader", "device_name": "Sage TUI"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _browser_cookie(client: TestClient, repository: CloudRepository) -> dict[str, str]:
    await repository.create_invite("legacy-browser", email="browser@example.com")
    user = await repository.get_or_create_identity(
        provider="github",
        provider_subject="legacy-browser-subject",
        email="browser@example.com",
        display_name="Legacy Browser",
        invite_code="legacy-browser",
    )
    token = "legacy-browser-session"
    await repository.create_session(
        user.user_id,
        token,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    client.cookies.set("sage_session", token)
    return {}


async def test_production_coding_rest_routes_require_authentication(tmp_path: Path) -> None:
    client, _repository, engine = await _production_client(tmp_path)
    try:
        created = client.post("/api/v1/coding/session", json={})
        listed = client.get("/api/v1/coding/sessions")
        models = client.get("/api/v1/coding/models")
    finally:
        await engine.dispose()

    assert created.status_code == 401
    assert listed.status_code == 401
    assert models.status_code == 401
    assert created.json() == {"detail": "cloud authentication is required"}


async def test_production_coding_routes_accept_valid_cloud_session(tmp_path: Path) -> None:
    client, repository, engine = await _production_client(tmp_path)
    try:
        await repository.create_invite("coding-owner", email="owner@example.com")
        user = await repository.get_or_create_identity(
            provider="github",
            provider_subject="coding-owner-subject",
            email="owner@example.com",
            display_name="Coding Owner",
            invite_code="coding-owner",
        )
        token = "valid-production-session"
        await repository.create_session(
            user.user_id,
            token,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        client.cookies.set("sage_session", token)

        response = client.get("/api/v1/coding/sessions")
    finally:
        await engine.dispose()

    assert response.status_code == 200
    assert response.json() == {"sessions": []}


async def test_production_coding_routes_fail_closed_without_control_plane(
    tmp_path: Path,
) -> None:
    app = create_app(
        cloud_repository=object(),
        cloud_app_env="production",
        cloud_token_secret="test-only-jwt-signing-secret-that-is-long-enough",
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )

    response = TestClient(app).get("/api/v1/coding/models")

    assert response.status_code == 503
    assert response.json() == {"detail": "cloud control plane is unavailable"}


async def test_production_coding_websocket_rejects_anonymous_client(
    tmp_path: Path,
) -> None:
    client, _repository, engine = await _production_client(tmp_path)
    try:
        with (
            pytest.raises(WebSocketDisconnect) as exc_info,
            client.websocket_connect("/api/v1/coding/unknown/stream"),
        ):
            pass
    finally:
        await engine.dispose()

    assert exc_info.value.code == 1008


@pytest.mark.parametrize("auth_mode", ["bearer", "cookie"])
async def test_production_fails_closed_for_unowned_legacy_session(
    tmp_path: Path,
    auth_mode: str,
) -> None:
    client, repository, engine = await _production_client(tmp_path)
    client.app.state.cloud_canary_invite_login_enabled = True
    _save_unowned_session(client, tmp_path)
    headers = (
        await _device_bearer(client, repository)
        if auth_mode == "bearer"
        else await _browser_cookie(client, repository)
    )
    try:
        listed = client.get("/api/v1/coding/sessions", headers=headers)
        requests = (
            client.post("/api/v1/coding/session/legacy-unowned/resume", headers=headers),
            client.get("/api/v1/coding/session/legacy-unowned/timeline?limit=10", headers=headers),
            client.get("/api/v1/coding/session/legacy-unowned/messages", headers=headers),
            client.patch(
                "/api/v1/coding/session/legacy-unowned/metadata",
                headers=headers,
                json={"title": "must not change"},
            ),
            client.get("/api/v1/coding/legacy-unowned/files", headers=headers),
        )
        with (
            pytest.raises(WebSocketDenialResponse) as exc_info,
            client.websocket_connect(
                "/api/v1/coding/legacy-unowned/stream",
                headers=headers,
            ),
        ):
            pass
    finally:
        await engine.dispose()

    assert listed.status_code == 200
    assert listed.json() == {"sessions": []}
    assert [response.status_code for response in requests] == [404, 404, 404, 404, 404]
    assert exc_info.value.status_code == 404


@pytest.mark.parametrize("storage_state", ["missing", "corrupt"])
async def test_production_active_unowned_runtime_fails_closed_without_valid_storage(
    tmp_path: Path,
    storage_state: str,
) -> None:
    client, repository, engine = await _production_client(tmp_path)
    client.app.state.cloud_canary_invite_login_enabled = True
    headers = await _device_bearer(client, repository)
    try:
        created = client.post("/api/v1/coding/session", headers=headers, json={})
        assert created.status_code == 200
        session_id = created.json()["session_id"]
        runtime = client.app.state.coding_sessions[session_id]
        runtime.owner_user_id = None
        runtime.session.pop("owner_user_id", None)
        session_path = CodingSessionStore(client.app.state.coding_storage_root / "sessions").path(
            session_id
        )
        if storage_state == "missing":
            session_path.unlink()
        else:
            session_path.write_text("{broken", encoding="utf-8")

        responses = (
            client.post(f"/api/v1/coding/session/{session_id}/resume", headers=headers),
            client.get(f"/api/v1/coding/session/{session_id}/timeline", headers=headers),
            client.get(f"/api/v1/coding/session/{session_id}/messages", headers=headers),
            client.patch(
                f"/api/v1/coding/session/{session_id}/metadata",
                headers=headers,
                json={"title": "must not change"},
            ),
            client.get(f"/api/v1/coding/{session_id}/files", headers=headers),
        )
        with (
            pytest.raises(WebSocketDenialResponse) as denied,
            client.websocket_connect(
                f"/api/v1/coding/{session_id}/stream",
                headers=headers,
            ),
        ):
            pass
    finally:
        await engine.dispose()

    assert [response.status_code for response in responses] == [404, 404, 404, 404, 404]
    assert denied.value.status_code == 404


async def test_development_keeps_unowned_legacy_session_compatible(tmp_path: Path) -> None:
    client, _repository, engine = await _production_client(tmp_path)
    client.app.state.cloud_app_env = "development"
    _save_unowned_session(client, tmp_path)
    try:
        listed = client.get("/api/v1/coding/sessions")
        messages = client.get("/api/v1/coding/session/legacy-unowned/messages")
        metadata = client.patch(
            "/api/v1/coding/session/legacy-unowned/metadata",
            json={"title": "Local history"},
        )
    finally:
        await engine.dispose()

    assert [item["session_id"] for item in listed.json()["sessions"]] == ["legacy-unowned"]
    assert messages.status_code == 200
    assert messages.json()["messages"][0]["content"] == "private legacy content"
    assert metadata.status_code == 200
