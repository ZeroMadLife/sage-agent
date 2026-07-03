"""generate_itinerary 工具 — 包装 Phase 2 多Agent图。

两段式架构的关键组件：
- 主Agent(ReAct)看到的只是一个普通异步函数
- 内部启动 Phase 2 的 LangGraph 多Agent协作（info+recommend→planning→budget）
- 主Agent不知道内部是多Agent

调用方式：
    tool = create_itinerary_tool(graph=compiled_graph)
    result = await tool(destination="杭州", budget_total=500, ...)
    # result 是可序列化的 dict, 含行程+天气
"""

import logging
import re
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from models.itinerary import Itinerary

logger = logging.getLogger(__name__)
DEFAULT_BUDGET_TOTAL = 500
DEFAULT_DURATION_DAYS = 2

PREFERENCE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "美食": ("美食", "吃", "小吃", "海鲜", "餐厅", "饭"),
    "自然风光": ("自然", "山水", "湖", "海", "风景", "公园"),
    "人文古迹": ("历史", "古迹", "寺庙", "博物馆", "文化", "古城"),
    "购物": ("购物", "逛街", "买", "商场"),
}

CHINESE_DIGITS: dict[str, int] = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _to_budget(value: Any) -> int:
    """Normalize loose LLM budget arguments into an integer CNY amount."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    match = re.search(r"\d{2,6}", str(value))
    return int(match.group(0)) if match else DEFAULT_BUDGET_TOTAL


def _chinese_number_to_int(value: str) -> int:
    """Convert small Chinese numerals used for trip durations."""
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + CHINESE_DIGITS.get(value[-1], 0)
    if "十" in value:
        left, _, right = value.partition("十")
        tens = CHINESE_DIGITS.get(left, 1)
        ones = CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    return CHINESE_DIGITS.get(value, 0)


def _extract_duration_days(value: Any) -> int:
    """Extract duration days from numbers or loose Chinese text."""
    if isinstance(value, int):
        return max(value, 1)
    if isinstance(value, float):
        return max(int(value), 1)

    text = str(value)
    digit_match = re.search(r"(\d{1,2})\s*(?:天|日)", text)
    if digit_match:
        return max(int(digit_match.group(1)), 1)

    chinese_match = re.search(r"([一二两三四五六七八九十]{1,3})\s*(?:天|日)", text)
    if chinese_match:
        return max(_chinese_number_to_int(chinese_match.group(1)), 1)
    return 0


def _default_dates(duration_days: int) -> dict[str, str]:
    """Create a near-future date range when the LLM omitted concrete dates."""
    safe_days = max(duration_days, 1)
    start = date.today() + timedelta(days=1)
    end = start + timedelta(days=safe_days - 1)
    return {"start": start.isoformat(), "end": end.isoformat()}


def _normalize_dates(value: Any, duration_days: int) -> dict[str, str]:
    """Normalize date arguments to the graph's expected dict shape."""
    if isinstance(value, Mapping):
        start = value.get("start", "")
        end = value.get("end", "")
        if start or end:
            return {"start": str(start), "end": str(end or start)}

    extracted_days = _extract_duration_days(value)
    return _default_dates(extracted_days or duration_days or DEFAULT_DURATION_DAYS)


def _normalize_preferences(value: Any) -> list[str]:
    """Normalize preference values from strings/lists into known labels."""
    raw_items: list[str] = []
    if isinstance(value, list):
        raw_items = [str(item) for item in value if str(item).strip()]
    elif isinstance(value, str):
        raw_items = [item for item in re.split(r"[、,，\s]+", value) if item]

    preferences: list[str] = []
    for item in raw_items:
        label = next(
            (
                preference
                for preference, keywords in PREFERENCE_KEYWORDS.items()
                if item == preference or any(keyword in item for keyword in keywords)
            ),
            item,
        )
        if label not in preferences:
            preferences.append(label)
    return preferences


def create_itinerary_tool(graph: Any) -> Any:
    """创建 generate_itinerary 工具函数。

    Args:
        graph: Phase 2 编译后的 LangGraph（build_graph() 的返回值）

    Returns:
        async 函数, 签名：(destination, budget_total, preferences, dates) -> dict
    """

    async def _generate_itinerary(
        destination: str,
        budget_total: Any = DEFAULT_BUDGET_TOTAL,
        preferences: Any = None,
        dates: Any = None,
        duration_days: Any = None,
    ) -> dict[str, Any]:
        """生成完整的多日行程方案。

        当用户需要完整行程规划时调用此工具。
        内部会启动多Agent协作：信息收集→景点推荐→行程规划→预算校验。

        Args:
            destination: 目的地城市, 如 "杭州""莆田"
            budget_total: 总预算（元）
            preferences: 偏好标签列表, 如 ["美食", "自然风光"]
            dates: 日期范围 {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}

        Returns:
            行程字典：{destination, days, total_cost, weather_summary, weather, budget}
            失败时返回 {error: str}
        """
        normalized_duration = _extract_duration_days(duration_days)
        normalized_budget = _to_budget(budget_total)
        normalized_preferences = _normalize_preferences(preferences)
        normalized_dates = _normalize_dates(dates, normalized_duration)
        initial_state: dict[str, Any] = {
            "messages": [],
            "destination": str(destination),
            "budget_total": normalized_budget,
            "preferences": normalized_preferences,
            "dates": normalized_dates,
            "iteration_count": 0,
        }

        try:
            result = await graph.ainvoke(initial_state)
        except Exception as exc:
            logger.error("generate_itinerary 内部 graph 执行失败: %s", exc)
            return {"error": str(exc)}

        itinerary = result.get("itinerary")
        if not isinstance(itinerary, Itinerary):
            return {"error": "Agent未能生成有效行程"}

        weather_info = result.get("weather_info", {})

        return {
            "destination": itinerary.destination,
            "days": [day.model_dump() for day in itinerary.days],
            "total_cost": itinerary.total_cost,
            "weather_summary": itinerary.weather_summary,
            "weather": {
                "current": weather_info.get("current"),
                "error": weather_info.get("error", False),
            },
            "budget": itinerary.budget.model_dump() if itinerary.budget else None,
        }

    return _generate_itinerary
