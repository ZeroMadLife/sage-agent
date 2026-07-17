"""API contract and ownership coverage for the V7 assistant home."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from core.cloud.auth.repository import CloudRepository
from core.coding.persistence import CodingSessionStore
from core.knowledge import KnowledgeSourceRoot
from db.database import create_engine, create_session_factory
from db.migrations import init_db


@pytest.fixture
async def repository() -> AsyncIterator[CloudRepository]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    factory = create_session_factory(engine)
    await init_db(engine)
    try:
        yield CloudRepository(factory)
    finally:
        await engine.dispose()


def _app(
    tmp_path: Path,
    *,
    repository: CloudRepository | None = None,
    app_env: str = "development",
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return create_app(
        coding_workspace_root=workspace,
        coding_storage_root=tmp_path / ".coding",
        cloud_repository=repository,
        cloud_dev_login_enabled=True,
        cloud_app_env=app_env,
    )


def _save_session(
    app,
    *,
    session_id: str,
    owner_user_id: str | None,
    workspace_root: Path,
) -> None:
    CodingSessionStore(app.state.coding_storage_root / "sessions").save(
        {
            "id": session_id,
            "workspace_root": str(workspace_root),
            "owner_user_id": owner_user_id,
            "created_at": "2026-07-15T00:00:00+00:00",
            "updated_at": "2026-07-15T01:00:00+00:00",
            "history": [
                {
                    "role": "user",
                    "content": "复盘 Sage",
                    "created_at": "2026-07-15T01:00:00+00:00",
                }
            ],
            "runtime_mode": {"mode": "default"},
        }
    )


async def _login(
    client: TestClient,
    repository: CloudRepository,
    *,
    email: str,
    invite: str,
) -> str:
    await repository.create_invite(invite, email=email)
    response = client.post(
        "/api/v1/cloud/auth/dev/login",
        json={"email": email, "display_name": "Sage Owner", "invite_code": invite},
    )
    assert response.status_code == 200
    return str(response.json()["user_id"])


def test_local_home_contract_is_honest_and_contains_no_secrets(tmp_path: Path) -> None:
    app = _app(tmp_path)
    response = TestClient(app).get("/api/v1/assistant/home")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["identity"] == {
        "mode": "local",
        "user_id": None,
        "display_name": "本地工作区",
    }
    assert body["knowledge"]["status"] == "not_configured"
    assert body["sessions"] == {
        "status": "empty",
        "items": [],
        "total": 0,
        "error": None,
    }
    assert body["projects"]["status"] == "unavailable"
    assert body["proposals"]["memory_pending"] == 0
    assert all(key not in response.text.lower() for key in ("secret", "api_key", "token"))


def test_local_home_projects_real_knowledge_counts_without_source_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    vault = tmp_path / "private-vault"
    vault.mkdir()
    (vault / "harness.md").write_text("# Agent Harness\n", encoding="utf-8")
    knowledge = tmp_path / "knowledge"
    app = create_app(
        coding_workspace_root=workspace,
        coding_storage_root=tmp_path / ".coding",
        cloud_app_env="development",
        knowledge_workspace_root=knowledge,
        knowledge_source_roots={
            "sage-learning": KnowledgeSourceRoot(
                root_id="sage-learning",
                kind="obsidian",
                label="Sage Learning",
                path=vault,
            )
        },
    )
    app.state.knowledge_store.ingest("sage-learning", "harness.md")

    response = TestClient(app).get("/api/v1/assistant/home")

    assert response.status_code == 200
    body = response.json()
    assert body["knowledge"] == {
        "status": "ready",
        "source_count": 1,
        "wiki_page_count": 0,
        "last_synced_at": None,
    }
    assert body["proposals"]["wiki_pending"] == 1
    assert body["suggested_actions"][0]["id"] == "review-wiki"
    assert str(vault) not in response.text


async def test_cloud_home_uses_cookie_identity_and_owner_scoped_data(
    tmp_path: Path,
    repository: CloudRepository,
) -> None:
    app = _app(tmp_path, repository=repository)
    owner = TestClient(app)
    owner_user_id = await _login(
        owner, repository, email="owner@example.com", invite="owner-invite"
    )
    await repository.create_invite("other-invite", email="other@example.com")
    other_user = await repository.get_or_create_identity(
        provider="development",
        provider_subject="other@example.com",
        email="other@example.com",
        display_name="Other",
        invite_code="other-invite",
    )
    await repository.create_project(owner_user_id, "Sage")
    await repository.create_project(other_user.user_id, "Private")
    workspace = tmp_path / "workspace"
    _save_session(
        app,
        session_id="owner-session",
        owner_user_id=owner_user_id,
        workspace_root=workspace,
    )
    _save_session(
        app,
        session_id="other-session",
        owner_user_id=other_user.user_id,
        workspace_root=workspace,
    )

    response = owner.get("/api/v1/assistant/home")

    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["mode"] == "cloud"
    assert body["identity"]["user_id"] == owner_user_id
    assert [item["session_id"] for item in body["sessions"]["items"]] == [
        "owner-session"
    ]
    assert [item["name"] for item in body["projects"]["items"]] == ["Sage"]
    assert "other-session" not in response.text
    assert "Private" not in response.text


async def test_production_home_requires_a_valid_server_session(
    tmp_path: Path,
    repository: CloudRepository,
) -> None:
    app = _app(tmp_path, repository=repository, app_env="production")

    response = TestClient(app).get("/api/v1/assistant/home")

    assert response.status_code == 401
    assert response.json() == {"detail": "cloud authentication is required"}
