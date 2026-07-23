"""Authenticated cloud model Provider API tests."""

import socket
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from api.main import create_app
from core.cloud.auth.repository import CloudRepository
from core.cloud.model_providers import ModelProviderRepository, ProviderProbe
from db.database import create_engine, create_session_factory
from db.migrations import init_db


@dataclass
class Harness:
    cloud: CloudRepository
    providers: ModelProviderRepository
    probe: ProviderProbe
    engine: object


def _public_resolver(*_args: object) -> list[tuple[object, ...]]:
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    factory = create_session_factory(engine)
    await init_db(engine)

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("authorization") == "Bearer sk-owner-secret"
        return httpx.Response(200, json={"data": [{"id": "model-a"}, {"id": "model-b"}]})

    yield Harness(
        cloud=CloudRepository(factory),
        providers=ModelProviderRepository(factory, encryption_secret="provider-secret-" * 4),
        probe=ProviderProbe(
            app_env="production",
            resolver=_public_resolver,
            client_factory=lambda _destination: httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ),
        ),
        engine=engine,
    )
    await engine.dispose()


async def _client_for_user(
    harness: Harness,
    invite: str,
    email: str,
    *,
    coding_workspace_root: Path | None = None,
) -> TestClient:
    await harness.cloud.create_invite(invite, email=email)
    client = TestClient(
        create_app(
            cloud_repository=harness.cloud,
            cloud_model_provider_repository=harness.providers,
            cloud_model_provider_probe=harness.probe,
            cloud_dev_login_enabled=True,
            cloud_app_env="development",
            coding_workspace_root=coding_workspace_root,
            coding_storage_root=(
                coding_workspace_root / ".coding" if coding_workspace_root is not None else None
            ),
        )
    )
    response = client.post(
        "/api/v1/cloud/auth/dev/login",
        json={"email": email, "display_name": email, "invite_code": invite},
    )
    assert response.status_code == 200
    return client


def _create_payload() -> dict[str, object]:
    return {
        "name": "OpenAI Account",
        "api_mode": "openai_responses",
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-owner-secret",
        "default_model_id": "model-a",
        "models": [
            {
                "model_id": "model-a",
                "display_name": "Model A",
                "context_window_tokens": 128000,
                "output_reserve_tokens": 16000,
                "reasoning_supported": True,
            }
        ],
    }


async def test_provider_routes_require_authentication(harness: Harness) -> None:
    client = TestClient(
        create_app(
            cloud_repository=harness.cloud,
            cloud_model_provider_repository=harness.providers,
            cloud_model_provider_probe=harness.probe,
            cloud_dev_login_enabled=True,
            cloud_app_env="development",
        )
    )

    assert client.get("/api/v1/cloud/model-providers").status_code == 401


async def test_create_list_update_and_default_never_return_api_key(harness: Harness) -> None:
    client = await _client_for_user(harness, "owner-invite", "owner@example.com")

    created = client.post("/api/v1/cloud/model-providers", json=_create_payload())
    provider_id = created.json()["id"]
    listed = client.get("/api/v1/cloud/model-providers")
    updated = client.patch(
        f"/api/v1/cloud/model-providers/{provider_id}",
        json={"name": "OpenAI Production"},
    )

    assert created.status_code == 200
    assert created.json()["key_configured"] is True
    assert created.json()["key_hint"] == "••••cret"
    assert "api_key" not in created.text
    assert "encrypted" not in created.text
    assert "sk-owner-secret" not in created.text
    assert listed.json()["default_model"] == f"account:{provider_id}:model-a"
    assert updated.json()["name"] == "OpenAI Production"
    assert (
        await harness.providers.runtime_credentials(
            (await harness.cloud.authenticated_user(client.cookies.get("sage_session"))).user_id
        )
    )[0].api_key == "sk-owner-secret"


async def test_provider_test_and_discovery_return_bounded_public_results(
    harness: Harness,
) -> None:
    client = await _client_for_user(harness, "probe-invite", "probe@example.com")
    provider_id = client.post("/api/v1/cloud/model-providers", json=_create_payload()).json()["id"]

    tested = client.post(f"/api/v1/cloud/model-providers/{provider_id}/test")
    discovered = client.post(f"/api/v1/cloud/model-providers/{provider_id}/discover-models")

    assert tested.status_code == 200
    assert tested.json()["status"] == "connected"
    assert discovered.json() == {"models": ["model-a", "model-b"]}
    assert "sk-owner-secret" not in tested.text + discovered.text


async def test_cross_user_provider_ids_return_404(harness: Harness) -> None:
    owner = await _client_for_user(harness, "owner-two", "owner-two@example.com")
    other = await _client_for_user(harness, "other-two", "other-two@example.com")
    provider_id = owner.post("/api/v1/cloud/model-providers", json=_create_payload()).json()["id"]

    assert (
        other.patch(
            f"/api/v1/cloud/model-providers/{provider_id}", json={"name": "Stolen"}
        ).status_code
        == 404
    )
    assert other.post(f"/api/v1/cloud/model-providers/{provider_id}/test").status_code == 404
    assert other.delete(f"/api/v1/cloud/model-providers/{provider_id}").status_code == 404


async def test_provider_routes_reject_ssrf_and_default_deletion(harness: Harness) -> None:
    client = await _client_for_user(harness, "security-invite", "security@example.com")
    payload = _create_payload()
    payload["base_url"] = "http://169.254.169.254/latest"

    rejected = client.post("/api/v1/cloud/model-providers", json=payload)
    provider_id = client.post("/api/v1/cloud/model-providers", json=_create_payload()).json()["id"]
    deletion = client.delete(f"/api/v1/cloud/model-providers/{provider_id}")

    assert rejected.status_code == 422
    assert deletion.status_code == 409


async def test_invalid_provider_key_is_not_reflected_by_validation_errors(
    harness: Harness,
) -> None:
    client = await _client_for_user(harness, "key-validation-invite", "key-validation@example.com")
    payload = _create_payload()
    submitted_key = "sensitive-" + ("x" * 10_001)
    payload["api_key"] = submitted_key

    response = client.post("/api/v1/cloud/model-providers", json=payload)

    assert response.status_code == 422
    assert response.json() == {"detail": "API key is invalid"}
    assert submitted_key not in response.text


async def test_duplicate_provider_names_return_a_bounded_validation_error(
    harness: Harness,
) -> None:
    client = await _client_for_user(harness, "duplicate-name-invite", "duplicate-name@example.com")
    first = client.post("/api/v1/cloud/model-providers", json=_create_payload())
    duplicate = client.post("/api/v1/cloud/model-providers", json=_create_payload())

    second_payload = _create_payload()
    second_payload["name"] = "Anthropic Account"
    second = client.post("/api/v1/cloud/model-providers", json=second_payload)
    renamed = client.patch(
        f"/api/v1/cloud/model-providers/{second.json()['id']}",
        json={"name": "OpenAI Account"},
    )

    assert first.status_code == 200
    assert duplicate.status_code == 422
    assert duplicate.json() == {"detail": "Provider name already exists"}
    assert renamed.status_code == 422
    assert renamed.json() == {"detail": "Provider name already exists"}


async def test_account_default_model_bootstraps_coding_and_survives_resume(
    harness: Harness, tmp_path: Path
) -> None:
    client = await _client_for_user(
        harness,
        "coding-provider-invite",
        "coding-provider@example.com",
        coding_workspace_root=tmp_path,
    )
    created = client.post("/api/v1/cloud/model-providers", json=_create_payload())
    provider_id = created.json()["id"]
    runtime_model_id = f"account:{provider_id}:model-a"

    catalog = client.get("/api/v1/coding/models")
    session = client.post("/api/v1/coding/session", json={})
    session_id = session.json()["session_id"]
    reasoning = client.patch(f"/api/v1/coding/{session_id}/reasoning", json={"mode": "high"})

    assert catalog.status_code == 200
    assert catalog.json()["current"] == runtime_model_id
    assert any(model["id"] == runtime_model_id for model in catalog.json()["models"])
    assert session.status_code == 200
    assert client.app.state.coding_sessions[session_id].model_spec == runtime_model_id
    assert reasoning.status_code == 200
    assert reasoning.json()["reasoning_mode"] == "high"

    client.app.state.coding_sessions.clear()
    resumed = client.post(f"/api/v1/coding/session/{session_id}/resume")

    assert resumed.status_code == 200
    assert client.app.state.coding_sessions[session_id].model_spec == runtime_model_id
    assert client.app.state.coding_sessions[session_id].reasoning_mode == "high"
    assert client.app.state.coding_sessions[session_id].model.reasoning_effort == "high"


async def test_account_model_sessions_are_hidden_from_other_users(
    harness: Harness, tmp_path: Path
) -> None:
    owner = await _client_for_user(
        harness,
        "session-owner-invite",
        "session-owner@example.com",
        coding_workspace_root=tmp_path,
    )
    owner.post("/api/v1/cloud/model-providers", json=_create_payload())
    created = owner.post("/api/v1/coding/session", json={})
    session_id = created.json()["session_id"]

    await harness.cloud.create_invite("session-other-invite", email="session-other@example.com")
    other = TestClient(owner.app)
    login = other.post(
        "/api/v1/cloud/auth/dev/login",
        json={
            "email": "session-other@example.com",
            "display_name": "Other",
            "invite_code": "session-other-invite",
        },
    )

    assert login.status_code == 200
    assert owner.get(f"/api/v1/coding/{session_id}/files").status_code == 200
    assert other.get(f"/api/v1/coding/{session_id}/files").status_code == 404
    assert other.get(f"/api/v1/coding/session/{session_id}/timeline").status_code == 404
    assert other.get("/api/v1/coding/models", params={"session_id": session_id}).status_code == 404
    assert not other.get("/api/v1/coding/models").json()["current"].startswith("account:")
    assert other.post(f"/api/v1/coding/session/{session_id}/resume").status_code == 404
    with (
        pytest.raises(WebSocketDenialResponse) as denied,
        other.websocket_connect(f"/api/v1/coding/{session_id}/stream"),
    ):
        pass
    assert denied.value.status_code == 404


async def test_anonymous_coding_catalog_does_not_expose_account_models(
    harness: Harness,
) -> None:
    owner = await _client_for_user(harness, "catalog-owner-invite", "catalog-owner@example.com")
    provider_id = owner.post("/api/v1/cloud/model-providers", json=_create_payload()).json()["id"]
    anonymous = TestClient(
        create_app(
            cloud_repository=harness.cloud,
            cloud_model_provider_repository=harness.providers,
            cloud_model_provider_probe=harness.probe,
            cloud_dev_login_enabled=True,
            cloud_app_env="development",
        )
    )

    response = anonymous.get("/api/v1/coding/models")

    assert response.status_code == 200
    assert all(
        model["id"] != f"account:{provider_id}:model-a" for model in response.json()["models"]
    )


async def test_coding_catalog_does_not_decrypt_provider_keys(
    harness: Harness,
) -> None:
    client = await _client_for_user(harness, "catalog-key-invite", "catalog-key@example.com")
    client.post("/api/v1/cloud/model-providers", json=_create_payload())

    with patch.object(
        harness.providers,
        "runtime_credentials",
        AsyncMock(side_effect=AssertionError("catalog must not decrypt credentials")),
    ):
        response = client.get("/api/v1/coding/models")

    assert response.status_code == 200
    assert any(model["id"].startswith("account:") for model in response.json()["models"])
