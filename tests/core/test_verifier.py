"""Deterministic itinerary verifier tests."""

from core.verifier import verify_itinerary
from models.itinerary import BudgetBreakdown, Itinerary, ItineraryDay, SpotVisit


def _spot(name: str) -> SpotVisit:
    return SpotVisit(
        spot_id=name,
        name=name,
        arrival_time="09:00",
        departure_time="11:00",
        duration_hours=2,
    )


def test_verify_itinerary_passes_valid_plan() -> None:
    itinerary = Itinerary(
        destination="杭州",
        days=[
            ItineraryDay(date="2026-07-05", spots=[_spot("西湖")], total_cost=120),
            ItineraryDay(date="2026-07-06", spots=[_spot("河坊街")], total_cost=180),
        ],
        total_cost=300,
        budget=BudgetBreakdown(total=500, spent=300, over_budget=False),
    )

    result = verify_itinerary(
        itinerary=itinerary,
        dates={"start": "2026-07-05", "end": "2026-07-06"},
        budget_total=500,
        weather_info={"error": True},
    )

    assert result.passed is True
    assert result.issues == []


def test_verify_itinerary_detects_missing_date() -> None:
    itinerary = Itinerary(
        destination="杭州",
        days=[ItineraryDay(date="2026-07-05", spots=[_spot("西湖")], total_cost=120)],
        total_cost=120,
    )

    result = verify_itinerary(
        itinerary=itinerary,
        dates={"start": "2026-07-05", "end": "2026-07-06"},
        budget_total=500,
        weather_info={},
    )

    assert result.passed is False
    assert any(issue.code == "missing_date" for issue in result.issues)


def test_verify_itinerary_detects_budget_mismatch() -> None:
    itinerary = Itinerary(
        destination="杭州",
        days=[ItineraryDay(date="2026-07-05", spots=[_spot("西湖")], total_cost=120)],
        total_cost=999,
        budget=BudgetBreakdown(total=500, spent=999, over_budget=False),
    )

    result = verify_itinerary(
        itinerary=itinerary,
        dates={"start": "2026-07-05", "end": "2026-07-05"},
        budget_total=500,
        weather_info={},
    )

    assert result.passed is False
    assert {issue.code for issue in result.issues} >= {
        "total_cost_mismatch",
        "over_budget_flag_mismatch",
    }


def test_verify_itinerary_detects_empty_day() -> None:
    itinerary = Itinerary(
        destination="杭州",
        days=[ItineraryDay(date="2026-07-05", spots=[], total_cost=0)],
        total_cost=0,
    )

    result = verify_itinerary(
        itinerary=itinerary,
        dates={"start": "2026-07-05", "end": "2026-07-05"},
        budget_total=500,
        weather_info={},
    )

    assert result.passed is False
    assert any(issue.code == "empty_day" for issue in result.issues)
