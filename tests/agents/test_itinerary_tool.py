"""generate_itinerary 工具测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.itinerary_tool import create_itinerary_tool
from models.itinerary import Itinerary, ItineraryDay


@pytest.fixture
def mock_graph() -> MagicMock:
    """模拟 Phase 2 的编译后 graph。"""
    graph = MagicMock()
    graph.ainvoke = AsyncMock(return_value={
        "itinerary": Itinerary(
            destination="杭州",
            days=[ItineraryDay(date="2026-07-05", total_cost=200)],
            total_cost=200,
        ),
        "weather_info": {"current": {"temp_c": 28, "text": "晴"}, "error": False},
    })
    return graph


async def test_itinerary_tool_returns_itinerary_dict(mock_graph: MagicMock) -> None:
    """工具返回可序列化的行程字典（给主Agent用）。"""
    tool = create_itinerary_tool(graph=mock_graph)
    result = await tool(
        destination="杭州",
        budget_total=500,
        preferences=["美食"],
        dates={"start": "2026-07-05", "end": "2026-07-06"},
    )
    assert result["destination"] == "杭州"
    assert result["total_cost"] == 200
    assert len(result["days"]) == 1


async def test_itinerary_tool_passes_correct_state_to_graph(mock_graph: MagicMock) -> None:
    """工具正确构造 initial_state 传给 graph.ainvoke。"""
    tool = create_itinerary_tool(graph=mock_graph)
    await tool(
        destination="莆田",
        budget_total=300,
        preferences=["美食", "自然风光"],
        dates={"start": "2026-07-12", "end": "2026-07-13"},
    )
    call_args = mock_graph.ainvoke.call_args
    state = call_args.args[0]
    assert state["destination"] == "莆田"
    assert state["budget_total"] == 300
    assert "美食" in state["preferences"]
    assert state["iteration_count"] == 0


async def test_itinerary_tool_handles_graph_error() -> None:
    """graph 执行失败时返回错误字典（不抛异常, 让主Agent处理）。"""
    graph = MagicMock()
    graph.ainvoke = AsyncMock(side_effect=Exception("LLM超时"))
    tool = create_itinerary_tool(graph=graph)
    result = await tool(
        destination="杭州", budget_total=500,
        preferences=[], dates={"start": "2026-07-05", "end": "2026-07-06"},
    )
    assert "error" in result
    assert "LLM超时" in result["error"]


async def test_itinerary_tool_returns_weather_info(mock_graph: MagicMock) -> None:
    """工具返回天气信息（主Agent可以展示给用户）。"""
    tool = create_itinerary_tool(graph=mock_graph)
    result = await tool(
        destination="杭州", budget_total=500,
        preferences=[], dates={"start": "2026-07-05", "end": "2026-07-06"},
    )
    assert "weather" in result
    assert result["weather"]["current"]["temp_c"] == 28
