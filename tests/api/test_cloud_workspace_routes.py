"""Cloud workspace ownership API tests."""

from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from core.cloud.auth.repository import CloudRepository
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


async def _client_for_user(repository: CloudRepository, invite: str, email: str) -> TestClient:
    await repository.create_invite(invite, email=email)
    client = TestClient(
        create_app(
            cloud_repository=repository,
            cloud_dev_login_enabled=True,
            cloud_app_env="development",
        )
    )
    response = client.post(
        "/api/v1/cloud/auth/dev/login",
        json={"email": email, "display_name": email, "invite_code": invite},
    )
    assert response.status_code == 200
    return client


async def test_workspace_routes_require_authentication(repository: CloudRepository) -> None:
    client = TestClient(
        create_app(
            cloud_repository=repository,
            cloud_dev_login_enabled=True,
            cloud_app_env="development",
        )
    )

    response = client.get("/api/v1/cloud/projects")

    assert response.status_code == 401


async def test_owner_can_create_and_read_metadata_without_allocating_workspace_files(
    repository: CloudRepository,
) -> None:
    client = await _client_for_user(repository, "owner-invite", "owner@example.com")

    created_project = client.post("/api/v1/cloud/projects", json={"name": "Sage"})
    project_id = created_project.json()["project_id"]
    created_workspace = client.post(
        f"/api/v1/cloud/projects/{project_id}/workspaces", json={"provider": "cloud"}
    )
    workspace_id = created_workspace.json()["workspace_id"]
    fetched = client.get(f"/api/v1/cloud/workspaces/{workspace_id}")
    listed = client.get("/api/v1/cloud/projects")

    assert created_project.status_code == 200
    assert created_workspace.status_code == 200
    assert fetched.status_code == 200
    assert fetched.json()["lifecycle_state"] == "provisioning"
    assert "path" not in fetched.json()
    assert listed.json() == [{"project_id": project_id, "name": "Sage"}]


async def test_other_user_receives_404_for_guessed_workspace_id(
    repository: CloudRepository,
) -> None:
    owner = await _client_for_user(repository, "owner-invite", "owner@example.com")
    other = await _client_for_user(repository, "other-invite", "other@example.com")
    project_id = owner.post("/api/v1/cloud/projects", json={"name": "Private"}).json()["project_id"]
    workspace_id = owner.post(
        f"/api/v1/cloud/projects/{project_id}/workspaces", json={"provider": "cloud"}
    ).json()["workspace_id"]

    response = other.get(f"/api/v1/cloud/workspaces/{workspace_id}")

    assert response.status_code == 404


async def test_project_list_does_not_leak_another_users_metadata(
    repository: CloudRepository,
) -> None:
    owner = await _client_for_user(repository, "owner-list-invite", "owner-list@example.com")
    other = await _client_for_user(repository, "other-list-invite", "other-list@example.com")
    owner.post("/api/v1/cloud/projects", json={"name": "Private"})

    response = other.get("/api/v1/cloud/projects")

    assert response.status_code == 200
    assert response.json() == []
