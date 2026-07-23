"""Owner-scoped review and export contract for public publication candidates."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from core.cloud.auth.repository import CloudRepository
from core.publication import PublicationCandidateRepository, PublicationCandidateService
from db.database import create_engine, create_session_factory
from db.migrations import init_db


def _package(revision: str = "2026-07-23.1", content: str = "可公开的 Sage 学习记录") -> dict:
    return {
        "package_id": "sage-public",
        "revision": revision,
        "documents": [
            {
                "id": "sage-learning",
                "title": "Sage 学习记录",
                "url": "https://example.com/sage-learning",
                "revision": revision,
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
        ],
    }


@pytest.fixture
async def dependencies(
    tmp_path,
) -> AsyncIterator[tuple[CloudRepository, PublicationCandidateService]]:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'publication-api.sqlite3'}")
    factory = create_session_factory(engine)
    await init_db(engine)
    try:
        yield (
            CloudRepository(factory),
            PublicationCandidateService(PublicationCandidateRepository(factory)),
        )
    finally:
        await engine.dispose()


async def _client(
    cloud: CloudRepository,
    publication: PublicationCandidateService,
    *,
    invite: str,
    email: str,
) -> TestClient:
    await cloud.create_invite(invite, email=email)
    client = TestClient(
        create_app(
            cloud_repository=cloud,
            cloud_dev_login_enabled=True,
            cloud_app_env="development",
            publication_candidate_service=publication,
        )
    )
    login = client.post(
        "/api/v1/cloud/auth/dev/login",
        json={"email": email, "display_name": email, "invite_code": invite},
    )
    assert login.status_code == 200
    return client


async def test_owner_reviews_candidate_and_exports_exact_stage_request(dependencies) -> None:
    cloud, publication = dependencies
    client = await _client(
        cloud, publication, invite="publication-owner", email="owner@example.com"
    )
    created = client.post(
        "/api/v1/publication/candidates",
        json={
            "package": _package(),
            "reason": "公开发布前人工审阅",
            "evidence_refs": ["knowledge:page:sage-learning@r7"],
        },
    )
    candidate_id = created.json()["candidate_id"]

    pending_export = client.get(f"/api/v1/publication/candidates/{candidate_id}/stage-artifact")
    approved = client.post(
        f"/api/v1/publication/candidates/{candidate_id}/approve",
        json={"expected_revision": 1},
    )
    exported = client.get(f"/api/v1/publication/candidates/{candidate_id}/stage-artifact")
    detail = client.get(f"/api/v1/publication/candidates/{candidate_id}")
    listed = client.get("/api/v1/publication/candidates?status=approved")

    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    assert "package" not in created.json()
    assert pending_export.status_code == 409
    assert approved.status_code == 200
    assert approved.json()["revision"] == 2
    assert exported.status_code == 200
    assert exported.headers["cache-control"] == "no-store"
    assert exported.json()["stage_request"] == {"action": "stage", "package": _package()}
    assert detail.json()["package"] == _package()
    assert [event["event_type"] for event in detail.json()["events"]] == [
        "candidate_created",
        "candidate_approved",
    ]
    assert len(listed.json()["candidates"]) == 1
    assert "package" not in listed.json()["candidates"][0]


async def test_other_user_cannot_read_or_decide_candidate(dependencies) -> None:
    cloud, publication = dependencies
    owner = await _client(cloud, publication, invite="publication-a", email="a@example.com")
    other = await _client(cloud, publication, invite="publication-b", email="b@example.com")
    candidate_id = owner.post(
        "/api/v1/publication/candidates",
        json={"package": _package(), "reason": "", "evidence_refs": []},
    ).json()["candidate_id"]

    assert other.get(f"/api/v1/publication/candidates/{candidate_id}").status_code == 404
    assert (
        other.post(
            f"/api/v1/publication/candidates/{candidate_id}/approve",
            json={"expected_revision": 1},
        ).status_code
        == 404
    )
    assert other.get("/api/v1/publication/candidates").json() == {"candidates": []}


async def test_api_rejects_revision_replacement_and_forbidden_disclosure(dependencies) -> None:
    cloud, publication = dependencies
    client = await _client(
        cloud, publication, invite="publication-validation", email="safe@example.com"
    )
    first = client.post(
        "/api/v1/publication/candidates",
        json={"package": _package(), "reason": "", "evidence_refs": []},
    )
    replaced = client.post(
        "/api/v1/publication/candidates",
        json={
            "package": _package(content="不同内容"),
            "reason": "",
            "evidence_refs": [],
        },
    )
    forbidden = client.post(
        "/api/v1/publication/candidates",
        json={
            "package": _package(revision="2026-07-23.2", content="路径 /Users/private/vault"),
            "reason": "",
            "evidence_refs": [],
        },
    )

    assert first.status_code == 201
    assert replaced.status_code == 409
    assert forbidden.status_code == 422
    assert "forbidden disclosure" in forbidden.json()["detail"]


async def test_production_publication_routes_require_authentication(dependencies) -> None:
    cloud, publication = dependencies
    client = TestClient(
        create_app(
            cloud_repository=cloud,
            cloud_app_env="production",
            publication_candidate_service=publication,
        )
    )

    response = client.post(
        "/api/v1/publication/candidates",
        json={"package": _package(), "reason": "", "evidence_refs": []},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "cloud authentication is required"}
