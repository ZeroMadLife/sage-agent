"""Production route gates that must hold even when the frontend is bypassed."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from starlette.websockets import WebSocketDisconnect

from api.main import create_app
from core.cloud.auth.repository import CloudRepository
from core.knowledge import KnowledgeSourceRoot
from db.database import create_session_factory
from db.migrations import init_db


async def _repository(tmp_path: Path) -> CloudRepository:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cloud.db'}")
    await init_db(engine)
    factory = create_session_factory(engine)
    return CloudRepository(factory)


async def test_production_keeps_health_open_and_closes_legacy_chat(tmp_path: Path) -> None:
    repository = await _repository(tmp_path)
    client = TestClient(create_app(cloud_app_env="production", cloud_repository=repository))

    assert client.get("/health").status_code == 200
    response = client.post("/api/v1/chat", json={"content": "bypass"})
    assert response.status_code == 404
    assert response.json() == {"detail": "legacy chat is unavailable in production"}
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        client.websocket_connect("/api/v1/chat/missing/stream"),
    ):
        pass
    assert exc_info.value.code == 1008


async def test_production_knowledge_rejects_anonymous_requests(tmp_path: Path) -> None:
    repository = await _repository(tmp_path)
    workspace = tmp_path / "knowledge"
    source = tmp_path / "vault"
    workspace.mkdir()
    source.mkdir()
    app = create_app(
        cloud_app_env="production",
        cloud_repository=repository,
        knowledge_workspace_root=workspace,
        knowledge_database_path=workspace / ".sage" / "knowledge.sqlite3",
        knowledge_source_roots={
            "sage-learning": KnowledgeSourceRoot(
                root_id="sage-learning",
                kind="obsidian",
                label="Sage Learning",
                path=source,
            )
        },
        knowledge_jobs_enabled=False,
    )

    response = TestClient(app).get("/api/v1/knowledge")

    assert response.status_code == 401
    assert response.json() == {"detail": "cloud authentication is required"}
    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        TestClient(app).websocket_connect("/api/v1/knowledge/jobs/missing/stream"),
    ):
        pass
    assert exc_info.value.code == 1008
