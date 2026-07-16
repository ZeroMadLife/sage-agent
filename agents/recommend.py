"""Recommend Agent for budget-aware scenic spot ranking."""

from collections.abc import Callable
from typing import Any

from core.state import TravelState


def _numeric(value: Any) -> float:
    """Convert sortable numeric fields from scenic data into floats."""
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return 0.0


def recommend_node(
    state: TravelState,
    scenic_client: Any,
) -> dict[str, list[dict[str, Any]]]:
    """Search scenic spots and rank them by user budget and quality."""
    destination = str(state.get("destination", ""))
    preferences = state.get("preferences", [])
    budget_total = int(state.get("budget_total", 0))
    keywords = " ".join(preferences) if isinstance(preferences, list) else ""

    raw_spots = scenic_client.search_scenic_spots(
        city=destination,
        keywords=keywords,
        limit=20,
    )
    spots = [dict(spot) for spot in raw_spots]

    if budget_total < 200:
        spots.sort(
            key=lambda spot: (
                _numeric(spot.get("ticket_price", 0)),
                -_numeric(spot.get("rating", 0)),
            )
        )
    else:
        spots.sort(key=lambda spot: _numeric(spot.get("rating", 0)), reverse=True)

    return {"recommendations": spots}


def create_recommend_agent(
    scenic_client: Any,
) -> Callable[[TravelState], dict[str, list[dict[str, Any]]]]:
    """Create a LangGraph-compatible Recommend Agent node."""

    def _node(state: TravelState) -> dict[str, list[dict[str, Any]]]:
        return recommend_node(state, scenic_client)

    return _node
