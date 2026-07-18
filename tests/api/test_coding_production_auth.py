"""Production authentication boundary for Coding REST and WebSocket routes."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.main import create_app
from core.cloud.auth.repository import CloudRepository
from core.cloud.model_providers import ModelProviderRepository
from db.database import create_engine, create_session_factory
from db.migrations import init_db


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
        coding_workspace_root=tmp_path,
        coding_storage_root=tmp_path / ".coding",
    )
    return TestClient(app), repository, engine


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
