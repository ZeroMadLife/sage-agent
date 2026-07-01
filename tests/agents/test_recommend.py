"""Recommend Agent tests."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from agents.recommend import create_recommend_agent, recommend_node


@pytest.fixture
def mock_scenic_client() -> MagicMock:
    """Create a scenic client test double."""
    client = MagicMock()
    client.search_scenic_spots = MagicMock(
        return_value=[
            {"id": "1", "name": "西湖", "ticket_price": 0, "rating": 4.8, "category": "自然风光"},
            {
                "id": "2",
                "name": "灵隐寺",
                "ticket_price": 30,
                "rating": 4.7,
                "category": "人文古迹",
            },
            {"id": "3", "name": "河坊街", "ticket_price": 0, "rating": 4.3, "category": "美食购物"},
        ]
    )
    return client


def _state(preferences: list[str] | None = None, budget_total: int = 500) -> dict[str, Any]:
    return {
        "destination": "杭州",
        "preferences": preferences or [],
        "budget_total": budget_total,
        "messages": [],
    }


def test_recommend_node_returns_ranked_spots(mock_scenic_client: MagicMock) -> None:
    """Recommend node returns scenic spots ranked by rating."""
    result = recommend_node(_state(["自然风光"]), mock_scenic_client)

    spots = result["recommendations"]
    assert len(spots) == 3
    assert spots[0]["rating"] >= spots[1]["rating"]


def test_recommend_node_filters_by_budget(mock_scenic_client: MagicMock) -> None:
    """Low budgets prioritize free scenic spots."""
    result = recommend_node(_state(budget_total=100), mock_scenic_client)

    spots = result["recommendations"]
    assert spots[0]["ticket_price"] == 0
    assert spots[1]["ticket_price"] == 0


def test_recommend_node_handles_empty_preferences(mock_scenic_client: MagicMock) -> None:
    """Empty preferences still return candidate spots."""
    result = recommend_node(_state(), mock_scenic_client)

    assert len(result["recommendations"]) == 3


def test_create_recommend_agent_returns_callable_node(mock_scenic_client: MagicMock) -> None:
    """create_recommend_agent returns a LangGraph-compatible sync node."""
    node = create_recommend_agent(mock_scenic_client)

    result = node(_state())

    assert len(result["recommendations"]) == 3
