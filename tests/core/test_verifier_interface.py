"""Verifier interface tests."""

from typing import Any

from core.verifier import ItineraryVerifier, Verifier, verify_itinerary
from models.itinerary import BudgetBreakdown, Itinerary, ItineraryDay, SpotVisit


def _spot(name: str) -> SpotVisit:
    return SpotVisit(
        spot_id=name,
        name=name,
        arrival_time="09:00",
        departure_time="11:00",
        duration_hours=2,
    )


def _itinerary() -> Itinerary:
    return Itinerary(
        destination="杭州",
        days=[
            ItineraryDay(date="2026-07-05", spots=[_spot("西湖")], total_cost=120),
            ItineraryDay(date="2026-07-06", spots=[_spot("河坊街")], total_cost=180),
        ],
        total_cost=300,
        budget=BudgetBreakdown(total=500, spent=300, over_budget=False),
    )


def test_itinerary_verifier_implements_verifier_protocol() -> None:
    """ItineraryVerifier is a runtime-checkable Verifier implementation."""
    verifier = ItineraryVerifier()

    assert isinstance(verifier, Verifier)


def test_itinerary_verifier_matches_verify_itinerary_function() -> None:
    """The class wrapper should preserve the legacy function behavior."""
    itinerary = _itinerary()
    context: dict[str, Any] = {
        "dates": {"start": "2026-07-05", "end": "2026-07-06"},
        "budget_total": 500,
        "weather_info": {"error": True},
    }

    class_result = ItineraryVerifier().verify(itinerary, context)
    function_result = verify_itinerary(
        itinerary=itinerary,
        dates=context["dates"],
        budget_total=context["budget_total"],
        weather_info=context["weather_info"],
    )

    assert class_result == function_result
