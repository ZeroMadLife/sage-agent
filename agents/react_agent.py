"""TourSwarm 主Agent — ReAct 模式个人旅游助手。

两段式架构的主层：
- 日常对话和简单查询直接处理（调单个工具）
- 复杂行程规划调用 generate_itinerary 工具（内部启动多Agent协作）
- 支持多轮对话（传入 history）
- 工具调用过程通过 AgentResponse.tool_calls 返回（前端展示）

LLM 输出协议：
- 需要工具时输出 JSON：{"action": "工具名", "input": {"参数": "值"}}
- 不需要工具时直接输出回复文字
"""

import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
ToolStatus = Literal["running", "done", "error"]
ToolEventCallback = Callable[["ToolCallRecord"], Awaitable[None]]

AGENT_SYSTEM_PROMPT = """你是 TourSwarm, 一个学生穷游助手。

你能帮用户：查找附近美食/景点、规划行程、查天气、查路线、算预算。

行为原则：
1. 简单查询（"附近有什么好吃的""杭州天气"）→ 直接调工具查, 快速回复
2. 复杂规划（"帮我规划2日游""周末去XX"）→ 调 generate_itinerary 工具（会启动多Agent协作生成完整行程）
3. 用户说"换一个""加预算""第一天太多了"→ 基于对话上下文调整, 不用从头来
4. 总是先调工具获取真实信息, 不凭空编造
5. 预算敏感 — 推荐时考虑学生消费水平（人均15-50元餐饮, 免费景点优先）

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


class ToolCallRecord(BaseModel):
    """一次工具调用记录。"""

    tool: str = Field(description="工具名")
    input: dict[str, Any] = Field(default_factory=dict, description="调用参数")
    output: Any = Field(default=None, description="返回结果")
    error: str = Field(default="", description="错误信息, 空表示成功")
    status: ToolStatus = Field(default="done", description="工具执行状态")
    message: str = Field(default="", description="适合前端展示的人类可读状态")


class AgentResponse(BaseModel):
    """Agent 一次回复的完整结果。"""

    content: str = Field(description="回复文字")
    tool_calls: list[ToolCallRecord] = Field(default_factory=list, description="工具调用记录")
    itinerary: dict[str, Any] | None = Field(
        default=None, description="行程（generate_itinerary返回的）"
    )


class TourAgent:
    """ReAct 模式的旅游助手 Agent。"""

    def __init__(
        self,
        llm: ChatOpenAI,
        tools: dict[str, Any],
        max_iterations: int = 5,
    ) -> None:
        self._llm = llm
        self._tools = tools
        self._max_iterations = max_iterations

    async def chat(
        self,
        content: str,
        user_id: str,
        session_id: str,
        history: list[dict[str, str]] | None = None,
        on_tool_event: ToolEventCallback | None = None,
    ) -> AgentResponse:
        """处理用户消息, 返回回复+工具调用记录。

        Args:
            content: 用户消息
            user_id: 用户ID
            session_id: 会话ID
            history: 对话历史（多轮上下文）
            on_tool_event: 工具开始/完成/失败时的事件回调, 用于 WebSocket 流式展示

        Returns:
            AgentResponse（回复文字 + 工具调用记录 + 可选行程）
        """
        messages: list[dict[str, str]] = [
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": content})

        tool_calls: list[ToolCallRecord] = []

        for _ in range(self._max_iterations):
            response = await self._llm.ainvoke(messages)
            raw = getattr(response, "content", str(response))
            if not isinstance(raw, str):
                raw = str(raw)

            tool_call = self._try_parse_tool_call(raw)
            if tool_call is None:
                # 不需要工具, 直接回复 — 从历史tool_calls中提取行程
                itinerary_from_tool = self._extract_itinerary(tool_calls)
                return AgentResponse(
                    content=raw,
                    tool_calls=tool_calls,
                    itinerary=itinerary_from_tool,
                )

            tool_name = str(tool_call["action"])
            raw_input = tool_call.get("input", {})
            tool_input = raw_input if isinstance(raw_input, dict) else {}

            if tool_name not in self._tools:
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": f"工具 {tool_name} 不存在, 可用: {list(self._tools)}",
                    }
                )
                continue

            try:
                await self._emit_tool_event(
                    on_tool_event,
                    ToolCallRecord(
                        tool=tool_name,
                        input=tool_input,
                        status="running",
                        message=self._tool_message(tool_name, tool_input, "running"),
                    ),
                )
                result = await self._execute_tool(tool_name, tool_input)
                record = ToolCallRecord(
                    tool=tool_name,
                    input=tool_input,
                    output=result,
                    status="done",
                    message=self._tool_message(tool_name, tool_input, "done"),
                )
                tool_calls.append(record)
                await self._emit_tool_event(on_tool_event, record)

                # 如果是 generate_itinerary, 提取行程
                messages.append({"role": "assistant", "content": raw})
                result_str = json.dumps(result, ensure_ascii=False, default=str)
                messages.append(
                    {
                        "role": "user",
                        "content": f"工具 {tool_name} 返回: {result_str[:800]}",
                    }
                )
            except Exception as exc:
                logger.warning("工具 %s 执行失败: %s", tool_name, exc)
                record = ToolCallRecord(
                    tool=tool_name,
                    input=tool_input,
                    error=str(exc),
                    status="error",
                    message=self._tool_message(tool_name, tool_input, "error"),
                )
                tool_calls.append(record)
                await self._emit_tool_event(on_tool_event, record)
                messages.append({"role": "assistant", "content": raw})
                messages.append(
                    {
                        "role": "user",
                        "content": f"工具 {tool_name} 失败: {exc}, 请告知用户或尝试其他方式。",
                    }
                )

        # 循环结束（达到最大迭代次数）
        itinerary_from_tool = self._extract_itinerary(tool_calls)
        return AgentResponse(
            content="抱歉, 我尝试了多次但未能完成请求, 请重新描述你的需求。",
            tool_calls=tool_calls,
            itinerary=itinerary_from_tool,
        )

    async def _execute_tool(self, name: str, kwargs: dict[str, Any]) -> Any:
        """执行工具调用, 自动处理同步/异步工具。"""
        fn = self._tools[name]
        if inspect.iscoroutinefunction(fn):
            return await fn(**kwargs)
        return fn(**kwargs)

    @staticmethod
    def _extract_itinerary(tool_calls: list[ToolCallRecord]) -> dict[str, Any] | None:
        """从工具调用记录中提取 generate_itinerary 的行程结果。"""
        for tc in tool_calls:
            if (
                tc.tool == "generate_itinerary"
                and tc.error == ""
                and isinstance(tc.output, dict)
                and "error" not in tc.output
            ):
                return tc.output
        return None

    @staticmethod
    async def _emit_tool_event(
        callback: ToolEventCallback | None,
        record: ToolCallRecord,
    ) -> None:
        """Emit one tool status event when a streaming callback is provided."""
        if callback is not None:
            await callback(record)

    @staticmethod
    def _tool_message(tool_name: str, tool_input: dict[str, Any], status: ToolStatus) -> str:
        """Create a compact human-readable tool status message."""
        labels = {
            "search_nearby": "周边搜索",
            "get_poi_detail": "地点详情",
            "search_attractions": "景点搜索",
            "get_weather": "实时天气",
            "get_forecast": "天气预报",
            "get_route": "路线规划",
            "geocode": "地址定位",
            "search_scenic_spots": "本地景点库",
            "get_scenic_detail": "景点详情",
            "generate_itinerary": "多Agent行程规划",
        }
        label = labels.get(tool_name, tool_name)
        city = str(tool_input.get("city", "") or tool_input.get("destination", ""))
        days = tool_input.get("days")

        if tool_name == "get_forecast" and city:
            target = f"{city}未来 {days} 天天气" if days else f"{city}天气预报"
        elif tool_name == "get_weather" and city:
            target = f"{city}实时天气"
        elif tool_name == "generate_itinerary" and city:
            target = f"{city}行程方案"
        else:
            target = label

        if status == "running":
            return f"正在查询{target}" if "天气" in target else f"正在执行{target}"
        if status == "done":
            return target
        return f"{target}失败"

    @staticmethod
    def _try_parse_tool_call(text: str) -> dict[str, Any] | None:
        """尝试从 LLM 输出中解析工具调用JSON。"""
        text = text.strip()
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "action" in data:
                return data
        except json.JSONDecodeError:
            pass
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start)
            try:
                data = json.loads(text[start:end].strip())
                if isinstance(data, dict) and "action" in data:
                    return data
            except json.JSONDecodeError:
                pass
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char != "{":
                continue
            try:
                data, _ = decoder.raw_decode(text[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict) and "action" in data:
                return data
        return None
