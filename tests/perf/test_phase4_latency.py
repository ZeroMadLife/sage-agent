"""Phase 4 latency budget tests for deterministic components."""

import time

from core.verifier import verify_itinerary
from models.itinerary import Itinerary, ItineraryDay, SpotVisit


def test_verifier_p95_proxy_is_under_50ms() -> None:
    itinerary = Itinerary(
        destination="杭州",
        days=[
            ItineraryDay(
                date="2026-07-05",
                spots=[
                    SpotVisit(
                        spot_id="xihu",
                        name="西湖",
                        arrival_time="09:00",
                        departure_time="11:00",
                        duration_hours=2,
                    )
                ],
                total_cost=0,
            )
        ],
        total_cost=0,
    )

    started = time.perf_counter()
    for _ in range(100):
        verify_itinerary(
            itinerary=itinerary,
            dates={"start": "2026-07-05", "end": "2026-07-05"},
            budget_total=500,
            weather_info={},
        )
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert elapsed_ms < 50
