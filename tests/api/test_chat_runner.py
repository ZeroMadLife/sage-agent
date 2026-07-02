"""Chat runner tests."""

from typing import Any

from api.schemas import ProgressEvent, ResultEvent
from api.services.chat_runner import run_chat
from models.itinerary import Itinerary, ItineraryDay


class GraphStub:
    async def ainvoke(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "itinerary": Itinerary(
                destination=str(state["destination"]),
                days=[ItineraryDay(date="2026-07-05", spots=[], total_cost=0)],
                total_cost=0,
            ),
            "dates": state["dates"],
            "budget_total": state["budget_total"],
            "weather_info": {},
        }


async def test_run_chat_emits_progress_and_result() -> None:
    events = [
        event
        async for event in run_chat(
            graph=GraphStub(),
            user_id="anonymous",
            session_id="session-001",
            content="周末去杭州2日游预算500元喜欢美食",
        )
    ]

    assert isinstance(events[0], ProgressEvent)
    assert events[0].agent == "supervisor"
    assert isinstance(events[-1], ResultEvent)
    assert events[-1].itinerary.destination == "杭州"
