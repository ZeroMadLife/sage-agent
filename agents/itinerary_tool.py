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
from typing import Any

from models.itinerary import Itinerary

logger = logging.getLogger(__name__)


def create_itinerary_tool(graph: Any) -> Any:
    """创建 generate_itinerary 工具函数。

    Args:
        graph: Phase 2 编译后的 LangGraph（build_graph() 的返回值）

    Returns:
        async 函数, 签名：(destination, budget_total, preferences, dates) -> dict
    """

    async def _generate_itinerary(
        destination: str,
        budget_total: int,
        preferences: list[str],
        dates: dict[str, str],
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
        initial_state: dict[str, Any] = {
            "messages": [],
            "destination": destination,
            "budget_total": budget_total,
            "preferences": preferences,
            "dates": dates,
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
