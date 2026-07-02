"""FastAPI contract schema tests."""

from api.schemas import ChatRequest, ChatStartResponse, ProgressEvent, ResultEvent
from models.itinerary import BudgetBreakdown, Itinerary, ItineraryDay


def test_chat_request_defaults_to_anonymous_user() -> None:
    request = ChatRequest(content="周末去杭州2日游预算500元喜欢美食")

    assert request.content == "周末去杭州2日游预算500元喜欢美食"
    assert request.user_id == "anonymous"


def test_chat_start_response_contains_session_id() -> None:
    response = ChatStartResponse(session_id="session-001")

    assert response.model_dump() == {"session_id": "session-001"}


def test_progress_event_serializes_agent_progress() -> None:
    event = ProgressEvent(agent="planning", message="正在生成行程")

    assert event.type == "progress"
    assert event.model_dump()["agent"] == "planning"


def test_result_event_contains_itinerary_and_validation() -> None:
    itinerary = Itinerary(
        destination="杭州",
        days=[ItineraryDay(date="2026-07-05", total_cost=120)],
        total_cost=120,
        budget=BudgetBreakdown(total=500, spent=120),
    )
    event = ResultEvent(
        itinerary=itinerary,
        validation={"passed": True, "issues": []},
        metrics={"latency_ms": 1200},
    )

    data = event.model_dump()
    assert data["type"] == "result"
    assert data["itinerary"]["destination"] == "杭州"
    assert data["validation"]["passed"] is True
