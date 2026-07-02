"""Info Agent for weather and scenic spot aggregation."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


async def info_node(
    state: dict[str, Any],
    weather_client: Any,
    scenic_client: Any,
) -> dict[str, Any]:
    """Fetch weather context and candidate scenic spots.

    Weather failures are degraded into an error marker so route planning can
    continue with scenic data instead of failing the whole graph.
    """
    destination = str(state.get("destination", ""))
    preferences = state.get("preferences", [])
    keywords = " ".join(preferences) if isinstance(preferences, list) else ""

    try:
        city_info = await weather_client.search_city(destination)
        location_id = str(city_info["location_id"])
        current = await weather_client.get_current_weather(location_id)
        forecast = await weather_client.get_forecast(location_id, days=7)
        weather_info: dict[str, Any] = {
            "current": current,
            "forecast": forecast,
            "error": False,
        }
    except Exception as exc:
        logger.warning("Weather lookup failed for %s: %s", destination, exc)
        weather_info = {
            "current": None,
            "forecast": [],
            "error": True,
            "message": f"天气信息暂不可用: {exc}",
        }

    spots = scenic_client.search_scenic_spots(
        city=destination,
        keywords=keywords,
        limit=20,
    )

    return {
        "weather_info": weather_info,
        "recommendations": spots,
    }


def create_info_agent(
    weather_client: Any,
    scenic_client: Any,
) -> Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Create a LangGraph-compatible Info Agent node."""

    async def _node(state: dict[str, Any]) -> dict[str, Any]:
        return await info_node(state, weather_client, scenic_client)

    return _node
