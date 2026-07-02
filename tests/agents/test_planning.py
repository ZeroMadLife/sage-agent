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


def _last_user_prompt(llm: MagicMock) -> str:
    messages = llm.ainvoke.await_args.args[0]
    return str(messages[-1]["content"])


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


async def test_planning_node_normalizes_llm_schema_drift() -> None:
    """LLM JSON with string meals/transport and numeric budget is normalized."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=(
                '{"destination": "杭州", "days": [{"date": "2026-07-05", '
                '"spots": [], "meals": "午餐：杭帮菜（50元）", '
                '"transport": "地铁和公交", "total_cost": 50}], '
                '"total_cost": 50, "weather_summary": "多云", "budget": 500}'
            )
        )
    )

    result = await planning_node(_state(), llm)

    itinerary = result["itinerary"]
    assert itinerary.budget is None
    assert itinerary.days[0].meals[0].name == "午餐：杭帮菜（50元）"
    assert itinerary.days[0].transport[0].mode == "transit"


async def test_planning_node_normalizes_partial_meal_objects() -> None:
    """LLM meal objects missing optional-looking fields receive safe defaults."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=(
                '{"destination": "杭州", "days": [{"date": "2026-07-05", '
                '"spots": [], "meals": [{"name": "早餐：葱包桧"}], '
                '"transport": [], "total_cost": 20}], "total_cost": 20, '
                '"weather_summary": "晴", "budget": null}'
            )
        )
    )

    result = await planning_node(_state(), llm)

    meal = result["itinerary"].days[0].meals[0]
    assert meal.name == "早餐：葱包桧"
    assert meal.meal_type == "other"
    assert meal.estimated_cost == 0


async def test_planning_node_normalizes_partial_spot_and_transport_objects() -> None:
    """Partial spot and transport objects receive safe defaults."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=(
                '{"destination": "杭州", "days": [{"date": "2026-07-05", '
                '"spots": [{"name": "西湖"}], "meals": [], '
                '"transport": [{"mode": "walking"}], "total_cost": 0}], '
                '"total_cost": 0, "weather_summary": "晴", "budget": null}'
            )
        )
    )

    result = await planning_node(_state(), llm)

    spot = result["itinerary"].days[0].spots[0]
    transport = result["itinerary"].days[0].transport[0]
    assert spot.spot_id == "西湖"
    assert spot.arrival_time == ""
    assert transport.from_name == ""
    assert transport.to_name == ""


async def test_planning_node_coerces_llm_scalar_field_types() -> None:
    """Scalar fields emitted by LLMs are coerced into model-compatible types."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content=(
                '{"destination": "杭州", "days": [{"date": "2026-07-05", '
                '"spots": [{"spot_id": 1, "name": "西湖", "arrival_time": 900, '
                '"departure_time": 1200, "duration_hours": "3", '
                '"ticket_price": "0", "category": 123, "location": 456}], '
                '"meals": [{"name": "午餐", "meal_type": 1, "estimated_cost": "50"}], '
                '"transport": [{"from_name": 1, "to_name": 2, "mode": 3, '
                '"distance_m": "100", "duration_s": "60"}], '
                '"total_cost": 50}], "total_cost": 50, '
                '"weather_summary": "晴", "budget": null}'
            )
        )
    )

    result = await planning_node(_state(), llm)

    day = result["itinerary"].days[0]
    assert day.spots[0].spot_id == "1"
    assert day.spots[0].duration_hours == 3.0
    assert day.meals[0].meal_type == "1"
    assert day.meals[0].estimated_cost == 50
    assert day.transport[0].distance_m == 100


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


def test_create_planning_prompt_includes_memory_context() -> None:
    """Planning prompt includes retrieved cross-session user preferences."""
    prompt = create_planning_prompt(
        destination="杭州",
        dates={"start": "2026-07-05", "end": "2026-07-06"},
        budget_total=500,
        preferences=["美食"],
        weather_info={"error": True},
        recommendations=[],
        memory_context="已知用户偏好: 用户喜欢海鲜; 预算500元以内",
    )

    assert "用户历史偏好" in prompt
    assert "海鲜" in prompt
    assert "预算500元以内" in prompt


async def test_planning_node_injects_state_memory_context(mock_llm: MagicMock) -> None:
    """planning_node passes state.memory_context into the LLM prompt."""
    state = {
        **_state(),
        "memory_context": "已知用户偏好: 用户喜欢海鲜",
    }

    await planning_node(state, mock_llm)

    assert "用户喜欢海鲜" in _last_user_prompt(mock_llm)


async def test_create_planning_agent_returns_callable_node(mock_llm: MagicMock) -> None:
    """create_planning_agent returns a LangGraph-compatible async node."""
    node = create_planning_agent(mock_llm)

    result = await node(_state())

    assert result["itinerary"].destination == "杭州"
