"""FastAPI route tests."""

from fastapi.testclient import TestClient

from api.main import create_app


def test_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_removed_legacy_chat_route_returns_not_found() -> None:
    client = TestClient(create_app())

    response = client.post("/api/v1/chat", json={"content": "legacy route"})

    assert response.status_code == 404
