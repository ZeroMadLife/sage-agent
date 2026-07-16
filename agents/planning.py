"""Planning Agent for LLM-powered itinerary generation."""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from core.state import TravelState
from models.itinerary import Itinerary

logger = logging.getLogger(__name__)

PLANNING_SYSTEM_PROMPT = """你是一个专业的旅游行程规划师。根据用户需求和约束生成详细行程。

输出要求：
1. 只输出严格 JSON，不要 Markdown 或解释文字。
2. JSON 必须符合 Itinerary 模型：destination、days、total_cost、weather_summary、budget。
3. 每个 day 包含 date、spots、meals、transport、total_cost。
4. 每个 spot 包含 spot_id、name、arrival_time、departure_time、duration_hours、ticket_price、category、location。

规划原则：
- 每天 3-5 个景点，避免过于紧凑。
- 根据天气调整室内/室外安排。
- 优先满足预算和用户偏好。
- total_cost 必须是所有 days 花费总和。
"""


def _to_str(value: Any, default: str = "") -> str:
    """Convert loose LLM scalar values into strings."""
    if value is None:
        return default
    return str(value)


def _to_int(value: Any, default: int = 0) -> int:
    """Convert loose LLM scalar values into integers."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    """Convert loose LLM scalar values into floats."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_named_items(value: Any, fallback_type: str) -> list[dict[str, Any]]:
    """Normalize LLM list-or-string fields into Pydantic-compatible dicts."""
    if isinstance(value, list):
        items: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            meal = dict(item)
            meal["name"] = _to_str(meal.get("name", ""))
            meal["meal_type"] = _to_str(meal.get("meal_type", fallback_type), fallback_type)
            meal["estimated_cost"] = _to_int(meal.get("estimated_cost", 0))
            items.append(meal)
        return items
    if isinstance(value, str) and value.strip():
        return [{"name": value.strip(), "meal_type": fallback_type, "estimated_cost": 0}]
    return []


def _normalize_transport(value: Any) -> list[dict[str, Any]]:
    """Normalize transport fields into a list of transport objects."""
    if isinstance(value, list):
        items: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            transport = dict(item)
            transport["from_name"] = _to_str(transport.get("from_name", ""))
            transport["to_name"] = _to_str(transport.get("to_name", ""))
            transport["mode"] = _to_str(transport.get("mode", "transit"), "transit")
            transport["distance_m"] = _to_int(transport.get("distance_m", 0))
            transport["duration_s"] = _to_int(transport.get("duration_s", 0))
            items.append(transport)
        return items
    if isinstance(value, str) and value.strip():
        return [
            {
                "from_name": "行程交通",
                "to_name": value.strip(),
                "mode": "transit",
                "distance_m": 0,
                "duration_s": 0,
            }
        ]
    return []


def _normalize_spots(value: Any) -> list[dict[str, Any]]:
    """Normalize spot fields into a list of spot visit objects."""
    if not isinstance(value, list):
        return []

    spots: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        spot = dict(item)
        name = _to_str(spot.get("name", ""))
        spot["spot_id"] = _to_str(spot.get("spot_id", spot.get("id", name)), name)
        spot["name"] = name
        spot["arrival_time"] = _to_str(spot.get("arrival_time", ""))
        spot["departure_time"] = _to_str(spot.get("departure_time", ""))
        spot["duration_hours"] = _to_float(spot.get("duration_hours", 1.0), 1.0)
        spot["ticket_price"] = _to_int(spot.get("ticket_price", 0))
        spot["category"] = _to_str(spot.get("category", ""))
        spot["location"] = _to_str(spot.get("location", ""))
        spots.append(spot)
    return spots


def _normalize_itinerary_data(data: Any) -> dict[str, Any]:
    """Normalize common LLM schema drift before Pydantic validation."""
    if not isinstance(data, dict):
        raise ValueError("LLM 返回的 JSON 顶层必须是对象")

    normalized = dict(data)
    days = normalized.get("days", [])
    normalized_days: list[dict[str, Any]] = []
    if isinstance(days, list):
        for item in days:
            if not isinstance(item, dict):
                continue
            day = dict(item)
            day["date"] = _to_str(day.get("date", ""))
            day["meals"] = _normalize_named_items(day.get("meals", []), "other")
            day["transport"] = _normalize_transport(day.get("transport", []))
            day["spots"] = _normalize_spots(day.get("spots", []))
            day["total_cost"] = _to_int(day.get("total_cost", 0))
            normalized_days.append(day)
    normalized["days"] = normalized_days
    normalized["destination"] = _to_str(normalized.get("destination", ""))
    normalized["total_cost"] = _to_int(normalized.get("total_cost", 0))
    normalized["weather_summary"] = _to_str(normalized.get("weather_summary", ""))

    if normalized.get("budget") is not None and not isinstance(normalized.get("budget"), dict):
        normalized["budget"] = None

    return normalized


def create_planning_prompt(
    destination: str,
    dates: dict[str, str],
    budget_total: int,
    preferences: list[str],
    weather_info: dict[str, Any],
    recommendations: list[dict[str, Any]],
    memory_context: str = "",
) -> str:
    """Build the planning prompt with all context required by the LLM."""
    current_weather = weather_info.get("current", {})
    weather_desc = "天气信息不可用"
    if not weather_info.get("error") and isinstance(current_weather, dict):
        weather_desc = (
            f"当前: {current_weather.get('text', '未知')} "
            f"{current_weather.get('temp_c', '?')}°C"
        )
        forecast = weather_info.get("forecast", [])
        if isinstance(forecast, list) and forecast:
            forecast_items = [
                (
                    f"{item.get('date', '')}: {item.get('text_day', '')} "
                    f"{item.get('temp_min', '')}-{item.get('temp_max', '')}°C"
                )
                for item in forecast
                if isinstance(item, dict)
            ]
            weather_desc = f"{weather_desc}\n预报: {'; '.join(forecast_items)}"

    spots_desc = "\n".join(
        (
            f"- {spot.get('name', '')} (ID: {spot.get('id', '')}, "
            f"门票: {spot.get('ticket_price', 0)}元, "
            f"评分: {spot.get('rating', 0)})"
        )
        for spot in recommendations
    )
    if not spots_desc:
        spots_desc = "无候选景点"

    prefs_desc = "、".join(preferences) if preferences else "无特殊偏好"
    memory_section = ""
    if memory_context:
        memory_section = f"\n用户历史偏好:\n{memory_context}\n"

    return f"""请为以下需求生成旅游行程：

目的地: {destination}
日期: {dates.get('start', '')} 至 {dates.get('end', '')}
预算: {budget_total}元
偏好: {prefs_desc}

天气信息:
{weather_desc}

候选景点:
{spots_desc}
{memory_section}

请生成行程方案，确保总花费不超过 {budget_total} 元。只输出 JSON，不要其他文字。"""


async def planning_node(state: TravelState, llm: Any) -> dict[str, Itinerary]:
    """Generate an itinerary with an LLM and validate it as an Itinerary."""
    prompt = create_planning_prompt(
        destination=str(state.get("destination", "")),
        dates=state.get("dates", {}),
        budget_total=int(state.get("budget_total", 0)),
        preferences=state.get("preferences", []),
        weather_info=state.get("weather_info", {}),
        recommendations=state.get("recommendations", []),
        memory_context=str(state.get("memory_context", "")),
    )
    messages = [
        {"role": "system", "content": PLANNING_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]

    response = await llm.ainvoke(messages)
    content = getattr(response, "content", response)
    content_text = content if isinstance(content, str) else str(content)

    try:
        data = json.loads(content_text)
    except json.JSONDecodeError as exc:
        logger.error("LLM returned invalid JSON: %s", content_text[:200])
        raise ValueError(f"LLM 返回的内容不是有效 JSON: {exc}") from exc

    normalized_data = _normalize_itinerary_data(data)
    return {"itinerary": Itinerary.model_validate(normalized_data)}


def create_planning_agent(llm: Any) -> Callable[[TravelState], Awaitable[dict[str, Itinerary]]]:
    """Create a LangGraph-compatible Planning Agent node."""

    async def _node(state: TravelState) -> dict[str, Itinerary]:
        return await planning_node(state, llm)

    return _node
