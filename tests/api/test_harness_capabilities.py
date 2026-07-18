"""Read-only Harness Capability Registry API tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sage_harness import (
    McpConfigSnapshot,
    McpManager,
    McpScope,
    McpServerConfig,
    McpToolDescriptor,
    WebFetchResult,
    WebSearchResult,
)

from api.main import create_app
from core.cloud.auth.repository import CloudRepository
from core.cloud.model_providers import ModelProviderRepository
from core.coding.memory import workspace_id_from_path
from core.coding.run_coordinator import RunEvent
from db.database import create_engine, create_session_factory
from db.migrations import init_db


class FakeModel:
    def __init__(self, *args: object, **kwargs: object) -> None:
        _ = args, kwargs


class FakeWebSearchPort:
    provider = "fake"
    available = True

    async def search(self, query: str, **_: object) -> WebSearchResult:
        return WebSearchResult(
            query=query,
            provider=self.provider,
            status="no_evidence",
            token_budget=2_000,
            used_tokens=0,
        )


class FakeWebFetchPort:
    available = True

    async def fetch(self, url: str) -> WebFetchResult:
        del url
        return WebFetchResult(status="unavailable", error_code="not_used")


class CountingTransport:
    def __init__(self) -> None:
        self.discovery_count = 0

    async def discover(
        self,
        server: McpServerConfig,
        scope: McpScope,
    ) -> Sequence[McpToolDescriptor]:
        _ = scope
        self.discovery_count += 1
        return [
            McpToolDescriptor.from_schema(
                tool_id=f"{server.name}:search_code",
                server_name=server.name,
                name=f"{server.name}_search_code",
                original_name="search_code",
                description="Search code",
                schema={"type": "object", "properties": {}},
            )
        ]

    async def invoke(
        self,
        tool: McpToolDescriptor,
        arguments: Mapping[str, object],
        scope: McpScope,
    ) -> object:
        _ = tool, arguments, scope
        return {}

    async def close_scope(self, scope: McpScope) -> None:
        _ = scope

    async def invalidate_revision(self, revision: str) -> None:
        _ = revision

    async def aclose(self) -> None:
        return None


@pytest.fixture
async def cloud_repositories() -> AsyncIterator[
    tuple[CloudRepository, ModelProviderRepository]
]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    factory = create_session_factory(engine)
    await init_db(engine)
    try:
        yield (
            CloudRepository(factory),
            ModelProviderRepository(
                factory,
                encryption_secret="test-provider-secret-" * 4,
            ),
        )
    finally:
        await engine.dispose()


def _app(workspace: Path, manager: McpManager | None = None):  # type: ignore[no-untyped-def]
    return create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=workspace,
        coding_storage_root=workspace / ".coding",
        coding_mcp_catalog=manager,
    )


def test_capability_api_reads_cached_mcp_catalog_without_discovery(tmp_path: Path) -> None:
    transport = CountingTransport()
    manager = McpManager(
        McpConfigSnapshot(
            revision="mcp-r1",
            servers=(McpServerConfig(name="github", transport="streamable_http"),),
        ),
        transport,
    )
    app = _app(tmp_path, manager)

    with TestClient(app) as client:
        session = client.post("/api/v1/coding/session", json={}).json()
        scope = McpScope(
            "local",
            workspace_id_from_path(tmp_path),
            session["session_id"],
        )
        asyncio.run(manager.catalog(scope))
        assert transport.discovery_count == 1

        response = client.get(
            "/api/v1/harness/capabilities",
            params={"session_id": session["session_id"], "surface": "coding"},
        )

        assert response.status_code == 200
        assert transport.discovery_count == 1
        payload = response.json()
        assert payload["session_id"] == session["session_id"]
        assert payload["workspace_id"] == workspace_id_from_path(tmp_path)
        assert payload["surface"] == "coding"
        assert payload["revision"]
        assert payload["count"] == len(payload["capabilities"])
        assert "mcp:github:search_code" in {
            item["capability_id"] for item in payload["capabilities"]
        }


def test_capability_api_isolates_workspace_skills_and_filters_surface(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "skills" / "alpha").mkdir(parents=True)
    (first / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: First skill\n---\nUse alpha.\n",
        encoding="utf-8",
    )
    app = _app(tmp_path)

    with TestClient(app) as client:
        first_id = client.post(
            "/api/v1/coding/session", json={"workspace_root": str(first)}
        ).json()["session_id"]
        second_id = client.post(
            "/api/v1/coding/session", json={"workspace_root": str(second)}
        ).json()["session_id"]

        first_payload = client.get(
            "/api/v1/harness/capabilities",
            params={"session_id": first_id, "surface": "coding", "origin": "skill"},
        ).json()
        second_payload = client.get(
            "/api/v1/harness/capabilities",
            params={"session_id": second_id, "surface": "coding", "origin": "skill"},
        ).json()
        knowledge_payload = client.get(
            "/api/v1/harness/capabilities",
            params={"session_id": first_id, "surface": "knowledge"},
        ).json()

    first_ids = {item["capability_id"] for item in first_payload["capabilities"]}
    second_ids = {item["capability_id"] for item in second_payload["capabilities"]}
    assert "skill:project:alpha" in first_ids
    assert "skill:project:alpha" not in second_ids
    assert first_ids - {"skill:project:alpha"} == second_ids
    assert all("knowledge" in item["surfaces"] for item in knowledge_payload["capabilities"])


def test_capability_api_rejects_unknown_session_and_invalid_filters(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    missing = client.get(
        "/api/v1/harness/capabilities",
        params={"session_id": "missing", "surface": "coding"},
    )
    invalid = client.get(
        "/api/v1/harness/capabilities",
        params={"session_id": "missing", "surface": "other"},
    )

    assert missing.status_code == 404
    assert invalid.status_code == 422


def test_capability_api_exposes_web_tools_only_with_server_ports(tmp_path: Path) -> None:
    disabled_root = tmp_path / "disabled"
    enabled_root = tmp_path / "enabled"
    disabled_root.mkdir()
    enabled_root.mkdir()
    disabled_app = _app(disabled_root)
    enabled_app = create_app(
        coding_model_factory=FakeModel,
        coding_workspace_root=enabled_root,
        coding_storage_root=enabled_root / ".coding",
        coding_web_fetch_port=FakeWebFetchPort(),
        coding_web_search_port=FakeWebSearchPort(),
    )

    def capability_ids(app) -> set[str]:  # type: ignore[no-untyped-def]
        with TestClient(app) as client:
            session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
            payload = client.get(
                "/api/v1/harness/capabilities",
                params={"session_id": session_id, "surface": "coding"},
            ).json()
        return {item["capability_id"] for item in payload["capabilities"]}

    assert "web:search" not in capability_ids(disabled_app)
    assert "web:fetch" not in capability_ids(disabled_app)
    assert "web:search" in capability_ids(enabled_app)
    assert "web:fetch" in capability_ids(enabled_app)


def test_capability_health_api_returns_content_free_workspace_metrics(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        session_id = client.post("/api/v1/coding/session", json={}).json()["session_id"]
        app.state.harness_capability_health_store.record_event(
            RunEvent(
                kind="harness",
                status="completed",
                payload={
                    "type": "capability_invocation_completed",
                    "version": 1,
                    "capability_id": "local:list_files",
                    "catalog_revision": "catalog-r1",
                    "status": "success",
                    "duration_ms": 42,
                },
                event_id="capability-health-event-1",
            ),
            owner_id="local",
            workspace_id=workspace_id_from_path(tmp_path),
            session_id=session_id,
            run_id="run-health",
        )

        response = client.get(
            "/api/v1/harness/capabilities/health",
            params={"session_id": session_id, "surface": "coding", "range": "30d"},
        )

    assert response.status_code == 200
    payload = response.json()
    metric = next(
        item
        for item in payload["capabilities"]
        if item["capability_id"] == "local:list_files"
    )
    assert payload["workspace_id"] == workspace_id_from_path(tmp_path)
    assert payload["range_days"] == 30
    assert metric["invocation_count"] == 1
    assert metric["success_count"] == 1
    assert metric["p50_latency_ms"] == 42
    assert "content" not in str(metric)


async def test_capability_api_hides_guessed_session_from_another_owner(
    tmp_path: Path,
    cloud_repositories: tuple[CloudRepository, ModelProviderRepository],
) -> None:
    cloud_repository, model_provider_repository = cloud_repositories
    await cloud_repository.create_invite("owner-invite", email="owner@example.com")
    await cloud_repository.create_invite("other-invite", email="other@example.com")
    options = {
        "cloud_repository": cloud_repository,
        "cloud_model_provider_repository": model_provider_repository,
        "cloud_dev_login_enabled": True,
        "cloud_app_env": "development",
        "coding_model_factory": FakeModel,
        "coding_workspace_root": tmp_path,
        "coding_storage_root": tmp_path / ".coding",
    }
    owner = TestClient(create_app(**options))
    other = TestClient(create_app(**options))
    assert (
        owner.post(
            "/api/v1/cloud/auth/dev/login",
            json={
                "email": "owner@example.com",
                "display_name": "Owner",
                "invite_code": "owner-invite",
            },
        ).status_code
        == 200
    )
    assert (
        other.post(
            "/api/v1/cloud/auth/dev/login",
            json={
                "email": "other@example.com",
                "display_name": "Other",
                "invite_code": "other-invite",
            },
        ).status_code
        == 200
    )
    session_id = owner.post("/api/v1/coding/session", json={}).json()["session_id"]

    response = other.get(
        "/api/v1/harness/capabilities",
        params={"session_id": session_id, "surface": "coding"},
    )
    health = other.get(
        "/api/v1/harness/capabilities/health",
        params={"session_id": session_id, "surface": "coding"},
    )

    assert response.status_code == 404
    assert health.status_code == 404
