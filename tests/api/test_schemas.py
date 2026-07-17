"""FastAPI contract schema tests."""

import pytest
from pydantic import ValidationError

from api.schemas import (
    AgentResultEvent,
    ChatRequest,
    ChatStartResponse,
    ProgressEvent,
    UserMessage,
)
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


def test_agent_result_event_contains_content_and_itinerary() -> None:
    itinerary = Itinerary(
        destination="杭州",
        days=[ItineraryDay(date="2026-07-05", total_cost=120)],
        total_cost=120,
        budget=BudgetBreakdown(total=500, spent=120),
    )
    event = AgentResultEvent(
        content="好的, 已为你规划杭州行程。",
        itinerary=itinerary,
        tool_calls=[{"tool": "generate_itinerary", "error": ""}],
        metrics={"latency_ms": 1200},
    )

    data = event.model_dump()
    assert data["type"] == "result"
    assert "杭州行程" in data["content"]
    assert data["itinerary"]["destination"] == "杭州"


def test_agent_result_event_without_itinerary() -> None:
    """纯文字回复时 itinerary 为 None。"""
    event = AgentResultEvent(content="你好！我是TourSwarm。")

    data = event.model_dump()
    assert data["type"] == "result"
    assert data["itinerary"] is None
    assert "TourSwarm" in data["content"]


def test_user_message_accepts_bounded_surface_context() -> None:
    message = UserMessage.model_validate(
        {
            "content": "解释当前页面",
            "surface_context": {
                "surface": "knowledge",
                "workspace_id": "knowledge-local",
                "resource": {
                    "type": "knowledge_page",
                    "id": "page-1",
                    "revision": "rev-1",
                    "label": "caller label",
                },
                "selection": {
                    "type": "graph_node",
                    "id": "node-1",
                    "revision": "rev-1",
                },
                "graph_revision": "graph-1",
                "operation_refs": [{"kind": "knowledge_job", "id": "job-1"}],
            },
        }
    )

    assert message.surface_context is not None
    assert message.surface_context.workspace_id == "knowledge-local"
    assert message.surface_context.operation_refs[0].id == "job-1"


def test_surface_context_rejects_unknown_fields_and_unbounded_refs() -> None:
    base = {
        "surface": "coding",
        "workspace_id": "workspace-1",
        "resource": {"type": "coding_workspace", "id": "workspace-1"},
        "selection": None,
        "operation_refs": [],
    }
    with pytest.raises(ValidationError):
        UserMessage.model_validate(
            {"content": "hello", "surface_context": {**base, "owner_id": "forged"}}
        )
    with pytest.raises(ValidationError):
        UserMessage.model_validate(
            {
                "content": "hello",
                "surface_context": {
                    **base,
                    "operation_refs": [
                        {"kind": "coding_run", "id": f"run-{index}"}
                        for index in range(21)
                    ],
                },
            }
        )
