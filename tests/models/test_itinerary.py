"""Itinerary Pydantic model tests."""

import json

from models.itinerary import (
    BudgetBreakdown,
    Itinerary,
    ItineraryDay,
    Meal,
    SpotVisit,
    Transport,
)


def test_spot_visit_creation() -> None:
    """Spot visit records can be created."""
    spot = SpotVisit(
        spot_id="hangzhou-xihu",
        name="西湖",
        arrival_time="09:00",
        departure_time="12:00",
        duration_hours=3.0,
        ticket_price=0,
        category="自然风光",
        location="120.141,30.246",
    )

    assert spot.name == "西湖"
    assert spot.ticket_price == 0
    assert spot.duration_hours == 3.0


def test_itinerary_day_creation() -> None:
    """Daily itinerary records can be created."""
    day = ItineraryDay(
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
        meals=[Meal(name="午餐", meal_type="lunch", estimated_cost=50)],
        transport=[
            Transport(
                from_name="西湖",
                to_name="灵隐寺",
                mode="driving",
                distance_m=7820,
                duration_s=1234,
            )
        ],
        total_cost=50,
    )

    assert len(day.spots) == 1
    assert day.total_cost == 50


def test_budget_breakdown_creation() -> None:
    """Budget breakdown can be created."""
    budget = BudgetBreakdown(
        total=500,
        spent=480,
        transport=150,
        accommodation=125,
        food=125,
        tickets=75,
        misc=25,
        over_budget=False,
    )

    assert budget.total == 500
    assert budget.over_budget is False


def test_itinerary_full_creation() -> None:
    """Full itinerary can be created."""
    itinerary = Itinerary(
        destination="杭州",
        days=[],
        total_cost=0,
        weather_summary="多云 24-32度",
        budget=BudgetBreakdown(
            total=500,
            spent=0,
            transport=150,
            accommodation=125,
            food=125,
            tickets=75,
            misc=25,
            over_budget=False,
        ),
    )

    assert itinerary.destination == "杭州"
    assert itinerary.budget is not None
    assert itinerary.budget.total == 500


def test_itinerary_serializes_to_json() -> None:
    """Itinerary can be serialized to JSON for frontend clients."""
    itinerary = Itinerary(
        destination="杭州",
        days=[],
        total_cost=0,
        weather_summary="晴",
        budget=BudgetBreakdown(
            total=500,
            spent=0,
            transport=150,
            accommodation=125,
            food=125,
            tickets=75,
            misc=25,
            over_budget=False,
        ),
    )

    data = itinerary.model_dump()
    assert data["destination"] == "杭州"
    assert data["budget"]["total"] == 500
    json_str = json.dumps(data, ensure_ascii=False)
    assert "杭州" in json_str


def test_budget_over_budget_flag() -> None:
    """Budget marks over-budget plans explicitly."""
    budget = BudgetBreakdown(
        total=500,
        spent=550,
        transport=150,
        accommodation=125,
        food=150,
        tickets=100,
        misc=25,
        over_budget=True,
    )

    assert budget.over_budget is True
