"""History REST route tests."""

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from api.main import create_app
from models.itinerary import Itinerary


def test_list_sessions_route_returns_user_sessions() -> None:
    """GET /api/v1/sessions returns session summaries for one user."""
    store = MagicMock()
    store.list_sessions = AsyncMock(
        return_value=[
            {
                "session_id": "session-001",
                "title": "杭州2日游",
                "created_at": "2026-07-03T00:00:00",
                "updated_at": "2026-07-03T00:00:00",
                "status": "active",
            }
        ]
    )
    client = TestClient(create_app(session_store=store))

    response = client.get("/api/v1/sessions", params={"user_id": "u_1"})

    assert response.status_code == 200
    assert response.json()["sessions"][0]["session_id"] == "session-001"
    store.list_sessions.assert_awaited_once_with("u_1", limit=20)


def test_get_session_messages_route_returns_history() -> None:
    """GET /api/v1/sessions/{id}/messages returns persisted messages."""
    store = MagicMock()
    store.get_session_messages = AsyncMock(
        return_value=[
            {
                "role": "user",
                "content": "杭州天气",
                "tool_calls": None,
                "created_at": "2026-07-03T00:00:00",
            }
        ]
    )
    client = TestClient(create_app(session_store=store))

    response = client.get("/api/v1/sessions/session-001/messages")

    assert response.status_code == 200
    assert response.json()["messages"][0]["content"] == "杭州天气"
    store.get_session_messages.assert_awaited_once_with("session-001")


def test_get_session_itineraries_route_returns_archived_plans() -> None:
    """GET /api/v1/sessions/{id}/itineraries returns archived plans."""
    store = MagicMock()
    store.list_itineraries = AsyncMock(
        return_value=[
            {
                "id": 1,
                "destination": "杭州",
                "total_cost": 200,
                "created_at": "2026-07-03T00:00:00",
                "content": Itinerary(destination="杭州", total_cost=200),
            }
        ]
    )
    client = TestClient(create_app(session_store=store))

    response = client.get("/api/v1/sessions/session-001/itineraries")

    assert response.status_code == 200
    assert response.json()["itineraries"][0]["destination"] == "杭州"
    store.list_itineraries.assert_awaited_once_with(session_id="session-001", user_id=None)


def test_get_user_itineraries_route_returns_archived_plans() -> None:
    """GET /api/v1/itineraries returns all archived plans for one user."""
    store = MagicMock()
    store.list_itineraries = AsyncMock(return_value=[])
    client = TestClient(create_app(session_store=store))

    response = client.get("/api/v1/itineraries", params={"user_id": "u_1"})

    assert response.status_code == 200
    assert response.json() == {"itineraries": []}
    store.list_itineraries.assert_awaited_once_with(user_id="u_1", session_id=None)
