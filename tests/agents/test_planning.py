"""Planning Agent tests."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.planning import create_planning_agent, create_planning_prompt, planning_node


@pytest.fixture
def mock_llm() -> MagicMock:
    """Create an LLM test double that returns itinerary JSON."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content='{"destination": "杭州", "days": [{"date": "2026-07-05", "spots": [{"spot_id": "hangzhou-xihu", "name": "西湖", "arrival_time": "09:00", "departure_time": "12:00", "duration_hours": 3.0, "ticket_price": 0, "category": "自然风光", "location": "120.141,30.246"}], "meals": [{"name": "午餐", "meal_type": "lunch", "estimated_cost": 50}], "transport": [], "total_cost": 50}], "total_cost": 50, "weather_summary": "多云 24-32度", "budget": null}'
        )
    )
    return llm


def _state() -> dict[str, Any]:
    return {
        "destination": "杭州",
        "dates": {"start": "2026-07-05", "end": "2026-07-06"},
        "budget_total": 500,
        "preferences": ["自然风光", "美食"],
        "weather_info": {"current": {"temp_c": 28, "text": "多云"}, "error": False},
        "recommendations": [{"id": "hangzhou-xihu", "name": "西湖", "ticket_price": 0}],
        "messages": [],
    }


async def test_planning_node_returns_itinerary(mock_llm: MagicMock) -> None:
    """planning_node returns a parsed Itinerary object."""
    result = await planning_node(_state(), mock_llm)

    assert result["itinerary"].destination == "杭州"
    assert len(result["itinerary"].days) == 1
    assert result["itinerary"].days[0].spots[0].name == "西湖"


async def test_planning_node_handles_llm_error() -> None:
    """Invalid LLM JSON raises a meaningful exception."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="这不是JSON"))

    with pytest.raises(ValueError, match="JSON"):
        await planning_node(_state(), llm)


def test_create_planning_prompt_includes_all_context() -> None:
    """Planning prompt includes destination, dates, budget, preferences, weather, and spots."""
    prompt = create_planning_prompt(
        destination="杭州",
        dates={"start": "2026-07-05", "end": "2026-07-06"},
        budget_total=500,
        preferences=["美食", "自然风光"],
        weather_info={"current": {"temp_c": 28, "text": "多云"}, "error": False},
        recommendations=[{"id": "x", "name": "西湖", "ticket_price": 0, "rating": 4.8}],
    )

    assert "杭州" in prompt
    assert "2026-07-05" in prompt
    assert "500" in prompt
    assert "美食" in prompt
    assert "多云" in prompt
    assert "西湖" in prompt


async def test_create_planning_agent_returns_callable_node(mock_llm: MagicMock) -> None:
    """create_planning_agent returns a LangGraph-compatible async node."""
    node = create_planning_agent(mock_llm)

    result = await node(_state())

    assert result["itinerary"].destination == "杭州"
