"""End-to-end tests for multi-agent collaboration."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.graph import build_graph
from core.memory.long_term import MemoryFact
from mcp_servers.scenic.client import ScenicClient


def _itinerary_payload(total_cost: int = 50) -> dict[str, Any]:
    return {
        "destination": "杭州",
        "days": [
            {
                "date": "2026-07-05",
                "spots": [
                    {
                        "spot_id": "hangzhou-xihu",
                        "name": "西湖",
                        "arrival_time": "09:00",
                        "departure_time": "12:00",
                        "duration_hours": 3.0,
                        "ticket_price": 0,
                        "category": "自然风光",
                        "location": "120.141,30.246",
                    }
                ],
                "meals": [{"name": "午餐", "meal_type": "lunch", "estimated_cost": 50}],
                "transport": [],
                "total_cost": total_cost,
            }
        ],
        "total_cost": total_cost,
        "weather_summary": "多云 24-32度",
        "budget": None,
    }


def _json_content(total_cost: int = 50) -> str:
    return json.dumps(_itinerary_payload(total_cost), ensure_ascii=False)


def _initial_state(budget_total: int = 500) -> dict[str, Any]:
    return {
        "messages": [],
        "destination": "杭州",
        "dates": {"start": "2026-07-05", "end": "2026-07-06"},
        "budget_total": budget_total,
        "preferences": ["自然风光", "美食"],
        "iteration_count": 0,
    }


@pytest.fixture
def mock_weather_client() -> MagicMock:
    """Create a weather client test double."""
    client = MagicMock()
    client.search_city = AsyncMock(return_value={"location_id": "101210101", "name": "杭州"})
    client.get_current_weather = AsyncMock(
        return_value={
            "temp_c": 28,
            "text": "多云",
            "humidity": 65,
            "wind_dir": "南风",
        }
    )
    client.get_forecast = AsyncMock(
        return_value=[{"date": "2026-07-05", "temp_max": 32, "temp_min": 24, "text_day": "多云"}]
    )
    return client


@pytest.fixture
def mock_scenic_client() -> ScenicClient:
    """Use local mock scenic data without network access."""
    return ScenicClient(
        data_path=str(Path(__file__).parent.parent.parent / "data" / "mock" / "scenic_spots.json")
    )


@pytest.fixture
def mock_planning_llm() -> MagicMock:
    """Create a planning LLM test double."""
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=_json_content()))
    return llm


@pytest.fixture
def mock_budget_llm() -> MagicMock:
    """Create a budget LLM test double."""
    return MagicMock()


async def test_e2e_within_budget(
    mock_weather_client: MagicMock,
    mock_scenic_client: ScenicClient,
    mock_planning_llm: MagicMock,
    mock_budget_llm: MagicMock,
) -> None:
    """Full graph creates an itinerary and budget breakdown within budget."""
    graph = build_graph(
        weather_client=mock_weather_client,
        scenic_client=mock_scenic_client,
        planning_llm=mock_planning_llm,
        budget_llm=mock_budget_llm,
    )

    result = await graph.ainvoke(_initial_state())

    assert result["itinerary"].destination == "杭州"
    assert len(result["itinerary"].days) == 1
    assert result["itinerary"].days[0].spots[0].name == "西湖"
    assert result["budget_breakdown"].over_budget is False


async def test_e2e_weather_degradation(
    mock_scenic_client: ScenicClient,
    mock_planning_llm: MagicMock,
    mock_budget_llm: MagicMock,
) -> None:
    """Weather lookup failures do not block itinerary generation."""
    weather_client = MagicMock()
    weather_client.search_city = AsyncMock(side_effect=Exception("API error"))

    graph = build_graph(
        weather_client=weather_client,
        scenic_client=mock_scenic_client,
        planning_llm=mock_planning_llm,
        budget_llm=mock_budget_llm,
    )

    result = await graph.ainvoke(_initial_state())

    assert result["weather_info"]["error"] is True
    assert result["itinerary"].destination == "杭州"


async def test_e2e_replans_when_over_budget(
    mock_weather_client: MagicMock,
    mock_scenic_client: ScenicClient,
    mock_budget_llm: MagicMock,
) -> None:
    """The compiled graph loops back to planning when budget is exceeded."""
    planning_llm = MagicMock()
    planning_llm.ainvoke = AsyncMock(
        side_effect=[
            MagicMock(content=_json_content(total_cost=600)),
            MagicMock(content=_json_content(total_cost=300)),
        ]
    )
    graph = build_graph(
        weather_client=mock_weather_client,
        scenic_client=mock_scenic_client,
        planning_llm=planning_llm,
        budget_llm=mock_budget_llm,
    )

    result = await graph.ainvoke(_initial_state(budget_total=500))

    assert planning_llm.ainvoke.await_count == 2
    assert result["budget_breakdown"].over_budget is False
    assert result["iteration_count"] == 2


async def test_e2e_injects_memory_before_planning(
    mock_weather_client: MagicMock,
    mock_scenic_client: ScenicClient,
    mock_budget_llm: MagicMock,
) -> None:
    """Providing a memory manager inserts memory before the planning node."""
    planning_llm = MagicMock()
    planning_llm.ainvoke = AsyncMock(return_value=MagicMock(content=_json_content()))
    memory_manager = MagicMock()
    memory_manager.retrieve_for_planning = MagicMock(return_value="已知用户偏好: 用户喜欢海鲜")
    memory_manager.retrieve_facts = MagicMock(
        return_value=[MemoryFact(content="用户喜欢海鲜", score=0.95, fact_id="mem_1")]
    )
    graph = build_graph(
        weather_client=mock_weather_client,
        scenic_client=mock_scenic_client,
        planning_llm=planning_llm,
        budget_llm=mock_budget_llm,
        memory_manager=memory_manager,
    )

    result = await graph.ainvoke(_initial_state())

    planning_llm.ainvoke.assert_awaited()
    call_args = planning_llm.ainvoke.await_args
    assert call_args is not None
    messages = call_args.args[0]
    assert "用户喜欢海鲜" in messages[-1]["content"]
    assert result["memory_context"] == "已知用户偏好: 用户喜欢海鲜"
    assert result["memory_facts"] == [
        {"content": "用户喜欢海鲜", "score": 0.95, "fact_id": "mem_1"}
    ]
