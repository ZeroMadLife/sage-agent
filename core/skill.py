"""Pluggable Skill definitions for the AgentRuntime."""

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from core.verifier import ItineraryVerifier, Verifier

TRAVEL_PLANNING_SKILL_NAME = "travel-planning"

TRAVEL_PLANNING_SYSTEM_PROMPT = """你是 TourSwarm, 一个学生穷游助手。

你能帮用户：查找附近美食/景点、规划行程、查天气、查路线、算预算。

行为原则：
1. 简单查询（"附近有什么好吃的""杭州天气"）→ 直接调工具查, 快速回复
2. 复杂规划（"帮我规划2日游""周末去XX"）→ 调 generate_itinerary 工具（会启动多Agent协作生成完整行程）
3. 用户说"换一个""加预算""第一天太多了"→ 基于对话上下文调整, 不用从头来
4. 总是先调工具获取真实信息, 不凭空编造
5. 预算敏感 — 推荐时考虑学生消费水平（人均15-50元餐饮, 免费景点优先）
6. 工具失败是可降级事件, 不是对话失败；不要重复追问用户已经提供过的目的地、天数、预算或偏好。
7. 当前用户直接问天气/温度/下雨时, 必须调用 get_weather 或 get_forecast, 不能只凭历史或常识回答。
8. 如果历史里刚查过同一目的地天气, 当前用户只是继续讨论出游/规划而不是重新问天气, 优先复用历史天气结论, 不要重复调用天气工具。

可用工具：
- search_nearby: 搜索附近POI（餐饮/景点/购物）。参数: location(经纬度), radius, keywords
- get_poi_detail: 获取POI详情（营业时间/电话/评分）。参数: poi_id
- search_attractions: 搜索城市景点。参数: city, keywords, limit
- get_weather: 查实时天气。参数: city
- get_forecast: 查7日预报。参数: city, days
- get_route: 查两点间路线。参数: origin, destination, mode
- geocode: 地址转坐标。参数: address, city
- search_scenic_spots: 搜索本地景点数据库。参数: city, category, keywords
- get_scenic_detail: 景点详情。参数: spot_id
- generate_itinerary: 生成完整多日行程（内部多Agent协作）。参数: destination, budget_total, preferences, dates

当你需要使用工具时, 只输出JSON（不要其他文字）：
{"action": "工具名", "input": {"参数": "值"}}

当你不需要工具时, 直接输出回复文字。"""


TOOL_DESCRIPTIONS: dict[str, str] = {
    "search_nearby": "搜索附近POI（餐饮/景点/购物）。",
    "get_poi_detail": "获取POI详情（营业时间/电话/评分）。",
    "search_attractions": "搜索城市景点。",
    "get_weather": "查询实时天气。",
    "get_forecast": "查询天气预报。",
    "get_route": "查询两点间路线。",
    "geocode": "地址转坐标。",
    "search_scenic_spots": "搜索本地景点数据库。",
    "get_scenic_detail": "查询景点详情。",
    "generate_itinerary": "生成完整多日行程，内部启动多Agent协作。",
}


class ToolSpec(BaseModel):
    """One callable tool exposed to a Skill."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str = ""
    handler: Any
    parameters: dict[str, Any] = Field(default_factory=dict)


class MemoryConfig(BaseModel):
    """Memory strategy attached to a Skill."""

    default_scope: str = "user"
    enable_short_term: bool = True
    enable_long_term: bool = True


class KnowledgeSource(BaseModel):
    """RAG knowledge source metadata reserved for later phases."""

    name: str
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class Skill(BaseModel):
    """A pluggable capability unit: prompt, tools, graph, verifier, memory, knowledge."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    system_prompt: str
    tools: list[ToolSpec]
    sub_agent_graph: Any | None
    verifier: Verifier | None
    memory_config: MemoryConfig
    knowledge_sources: list[KnowledgeSource]

    @property
    def tool_map(self) -> dict[str, Any]:
        """Return runtime callables keyed by tool name."""
        return {tool.name: tool.handler for tool in self.tools}


class SkillRegistry:
    """In-memory registry for available Skills."""

    def __init__(self, skills: Sequence[Skill] | None = None) -> None:
        self._skills: dict[str, Skill] = {}
        for skill in skills or []:
            self.register(skill)

    def register(self, skill: Skill) -> None:
        """Register or replace a Skill by name."""
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill:
        """Return a registered Skill by name."""
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"Skill not registered: {name}") from exc

    def names(self) -> list[str]:
        """Return registered Skill names in registration order."""
        return list(self._skills)


def build_travel_planning_skill(
    tools: Mapping[str, Any],
    sub_agent_graph: Any | None = None,
    verifier: Verifier | None = None,
) -> Skill:
    """Package existing tourism prompt/tools/graph/verifier as a Skill."""
    tool_specs = [
        ToolSpec(
            name=name,
            description=TOOL_DESCRIPTIONS.get(name, ""),
            handler=handler,
        )
        for name, handler in tools.items()
    ]
    return Skill(
        name=TRAVEL_PLANNING_SKILL_NAME,
        system_prompt=TRAVEL_PLANNING_SYSTEM_PROMPT,
        tools=tool_specs,
        sub_agent_graph=sub_agent_graph,
        verifier=verifier or ItineraryVerifier(),
        memory_config=MemoryConfig(default_scope=f"skill:{TRAVEL_PLANNING_SKILL_NAME}"),
        knowledge_sources=[],
    )
