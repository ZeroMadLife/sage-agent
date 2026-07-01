"""Budget Agent tests."""

from typing import Any
from unittest.mock import MagicMock

from agents.budget import budget_node, calculate_budget_breakdown
from models.itinerary import Itinerary, ItineraryDay, SpotVisit


def _make_itinerary(total_cost: int = 300) -> Itinerary:
    """Build an itinerary for budget tests."""
    return Itinerary(
        destination="杭州",
        days=[
            ItineraryDay(
                date="2026-07-05",
                spots=[
                    SpotVisit(
                        spot_id="hangzhou-xihu",
                        name="西湖",
                        arrival_time="09:00",
                        departure_time="12:00",
                        duration_hours=3.0,
                        ticket_price=0,
                        category="自然风光",
                        location="120.141,30.246",
                    )
                ],
                meals=[],
                transport=[],
                total_cost=total_cost,
            )
        ],
        total_cost=total_cost,
        weather_summary="晴",
    )


def _state(total_cost: int, budget_total: int = 500, iteration_count: int = 0) -> dict[str, Any]:
    return {
        "itinerary": _make_itinerary(total_cost=total_cost),
        "budget_total": budget_total,
        "iteration_count": iteration_count,
        "messages": [],
    }


def test_calculate_budget_within_budget() -> None:
    """Spending within budget is not marked over budget."""
    breakdown = calculate_budget_breakdown(
        itinerary=_make_itinerary(total_cost=300),
        budget_total=500,
        transport=150,
        accommodation=125,
        food=125,
        tickets=75,
        misc=25,
    )

    assert breakdown.over_budget is False
    assert breakdown.spent == 300


def test_calculate_budget_over_budget() -> None:
    """Spending over budget is marked over budget."""
    breakdown = calculate_budget_breakdown(
        itinerary=_make_itinerary(total_cost=600),
        budget_total=500,
        transport=150,
        accommodation=125,
        food=200,
        tickets=100,
        misc=25,
    )

    assert breakdown.over_budget is True
    assert breakdown.spent == 600


async def test_budget_node_returns_breakdown() -> None:
    """budget_node returns a budget breakdown."""
    result = await budget_node(_state(total_cost=300), MagicMock())

    assert result["budget_breakdown"].total == 500
    assert result["budget_breakdown"].over_budget is False
    assert result["over_budget"] is False


async def test_budget_node_flags_over_budget() -> None:
    """Over-budget itineraries set over_budget=True."""
    result = await budget_node(_state(total_cost=600), MagicMock())

    assert result["over_budget"] is True
    assert result["budget_breakdown"].over_budget is True


async def test_budget_node_increments_iteration() -> None:
    """Each budget check increments iteration_count."""
    result = await budget_node(_state(total_cost=600, iteration_count=1), MagicMock())

    assert result["iteration_count"] == 2
