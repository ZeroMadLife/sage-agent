"""Planning Agent for LLM-powered itinerary generation."""

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

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


def create_planning_prompt(
    destination: str,
    dates: dict[str, str],
    budget_total: int,
    preferences: list[str],
    weather_info: dict[str, Any],
    recommendations: list[dict[str, Any]],
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

    return f"""请为以下需求生成旅游行程：

目的地: {destination}
日期: {dates.get('start', '')} 至 {dates.get('end', '')}
预算: {budget_total}元
偏好: {prefs_desc}

天气信息:
{weather_desc}

候选景点:
{spots_desc}

请生成行程方案，确保总花费不超过 {budget_total} 元。只输出 JSON，不要其他文字。"""


async def planning_node(state: dict[str, Any], llm: Any) -> dict[str, Itinerary]:
    """Generate an itinerary with an LLM and validate it as an Itinerary."""
    prompt = create_planning_prompt(
        destination=str(state.get("destination", "")),
        dates=state.get("dates", {}),
        budget_total=int(state.get("budget_total", 0)),
        preferences=state.get("preferences", []),
        weather_info=state.get("weather_info", {}),
        recommendations=state.get("recommendations", []),
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

    return {"itinerary": Itinerary.model_validate(data)}


def create_planning_agent(llm: Any) -> Callable[[dict[str, Any]], Awaitable[dict[str, Itinerary]]]:
    """Create a LangGraph-compatible Planning Agent node."""

    async def _node(state: dict[str, Any]) -> dict[str, Itinerary]:
        return await planning_node(state, llm)

    return _node
