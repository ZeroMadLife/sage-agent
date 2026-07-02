"""Info Agent tests."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.info import create_info_agent, info_node


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
        return_value=[
            {"date": "2026-07-05", "temp_max": 32, "temp_min": 24, "text_day": "多云"},
            {"date": "2026-07-06", "temp_max": 34, "temp_min": 25, "text_day": "晴"},
        ]
    )
    return client


@pytest.fixture
def mock_scenic_client() -> MagicMock:
    """Create a scenic client test double."""
    client = MagicMock()
    client.search_scenic_spots = MagicMock(
        return_value=[
            {"id": "hangzhou-xihu", "name": "西湖", "ticket_price": 0, "rating": 4.8},
            {"id": "hangzhou-lingyin", "name": "灵隐寺", "ticket_price": 30, "rating": 4.7},
        ]
    )
    return client


def _state(preferences: list[str] | None = None) -> dict[str, Any]:
    return {
        "destination": "杭州",
        "dates": {"start": "2026-07-05", "end": "2026-07-06"},
        "preferences": preferences or [],
        "messages": [],
    }


async def test_info_node_returns_weather_and_spots(
    mock_weather_client: MagicMock,
    mock_scenic_client: MagicMock,
) -> None:
    """info_node returns weather info and candidate scenic spots."""
    result = await info_node(_state(["自然风光"]), mock_weather_client, mock_scenic_client)

    assert result["weather_info"]["current"]["temp_c"] == 28
    assert len(result["weather_info"]["forecast"]) == 2
    assert result["weather_info"]["error"] is False
    assert len(result["recommendations"]) == 2


async def test_info_node_handles_weather_error(mock_scenic_client: MagicMock) -> None:
    """Weather failures degrade gracefully while scenic spots still return."""
    weather_client = MagicMock()
    weather_client.search_city = AsyncMock(side_effect=Exception("API error"))

    result = await info_node(_state(), weather_client, mock_scenic_client)

    assert result["weather_info"]["error"] is True
    assert "API error" in result["weather_info"]["message"]
    assert len(result["recommendations"]) == 2


async def test_info_node_searches_by_preferences(
    mock_weather_client: MagicMock,
    mock_scenic_client: MagicMock,
) -> None:
    """Preferences are converted into scenic search keywords."""
    await info_node(_state(["美食"]), mock_weather_client, mock_scenic_client)

    mock_scenic_client.search_scenic_spots.assert_called_once_with(
        city="杭州",
        keywords="美食",
        limit=20,
    )


async def test_create_info_agent_returns_callable_node(
    mock_weather_client: MagicMock,
    mock_scenic_client: MagicMock,
) -> None:
    """create_info_agent returns a LangGraph-compatible async node."""
    node = create_info_agent(mock_weather_client, mock_scenic_client)

    result = await node(_state())

    assert "weather_info" in result
    assert "recommendations" in result
