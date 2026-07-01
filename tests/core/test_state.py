"""TravelState tests."""

from typing import get_type_hints

from langgraph.graph import add_messages

from core.state import TravelState


def test_travel_state_has_required_fields() -> None:
    """TravelState includes all fields needed by Phase 2 agents."""
    annotations = TravelState.__annotations__
    required = [
        "messages",
        "user_id",
        "session_id",
        "intent",
        "destination",
        "budget_total",
        "dates",
        "preferences",
        "itinerary",
        "recommendations",
        "weather_info",
        "budget_breakdown",
        "iteration_count",
        "over_budget",
        "final_response",
    ]

    for field in required:
        assert field in annotations, f"Missing field: {field}"


def test_messages_uses_add_messages_reducer() -> None:
    """messages must use add_messages to merge agent message history."""
    hints = get_type_hints(TravelState, include_extras=True)
    messages_hint = hints["messages"]

    assert hasattr(messages_hint, "__metadata__")
    assert add_messages in messages_hint.__metadata__


def test_travel_state_is_typed_dict() -> None:
    """TravelState is a TypedDict-compatible class."""
    assert hasattr(TravelState, "__required_keys__")
    assert hasattr(TravelState, "__optional_keys__")
