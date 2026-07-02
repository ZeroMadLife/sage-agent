"""WebSocket route tests."""

from typing import Any

from fastapi.testclient import TestClient

from api.main import create_app
from models.itinerary import Itinerary, ItineraryDay


class GraphStub:
    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "itinerary": Itinerary(
                destination=str(state["destination"]),
                days=[ItineraryDay(date="2026-07-05", spots=[], total_cost=0)],
                total_cost=0,
            ),
            "weather_info": {},
        }


def test_chat_websocket_rejects_unknown_session() -> None:
    client = TestClient(create_app())

    with client.websocket_connect("/api/v1/chat/missing/stream") as websocket:
        event = websocket.receive_json()

    assert event["type"] == "error"
    assert "Unknown session" in event["message"]


def test_chat_websocket_streams_runner_events() -> None:
    client = TestClient(create_app(graph=GraphStub()))
    response = client.post("/api/v1/chat", json={"content": "周末去杭州2日游预算500元"})
    session_id = response.json()["session_id"]

    with client.websocket_connect(f"/api/v1/chat/{session_id}/stream") as websocket:
        first_event = websocket.receive_json()
        second_event = websocket.receive_json()
        third_event = websocket.receive_json()
        final_event = websocket.receive_json()

    assert first_event["type"] == "progress"
    assert second_event["type"] == "progress"
    assert third_event["type"] == "progress"
    assert final_event["type"] == "result"
