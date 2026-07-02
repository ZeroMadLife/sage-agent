"""FastAPI route tests."""

from fastapi.testclient import TestClient

from api.main import create_app


def test_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_start_chat_returns_session_id() -> None:
    client = TestClient(create_app())

    response = client.post("/api/v1/chat", json={"content": "周末去杭州"})

    assert response.status_code == 200
    assert response.json()["session_id"]
