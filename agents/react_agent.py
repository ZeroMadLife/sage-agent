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
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from core.skill import Skill, build_travel_planning_skill

logger = logging.getLogger(__name__)
ToolStatus = Literal["running", "done", "error"]
ToolEventCallback = Callable[["ToolCallRecord"], Awaitable[None]]

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


class AgentRuntime:
    """Generic ReAct runtime powered by an injected Skill."""

    def __init__(
        self,
        llm: ChatOpenAI,
        skill: Skill | None = None,
        tools: dict[str, Any] | None = None,
        max_iterations: int = 5,
    ) -> None:
        if skill is None:
            if tools is None:
                raise ValueError("AgentRuntime requires either skill or tools")
            skill = build_travel_planning_skill(tools=tools)
        self._llm = llm
        self._skill = skill
        self._tools = skill.tool_map
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
            {"role": "system", "content": self._skill.system_prompt},
        ]
        context_hint = self._build_travel_context_hint(content, history or [])
        if context_hint:
            messages.append({"role": "system", "content": context_hint})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": content})

        tool_calls: list[ToolCallRecord] = []
        weather_tool_retry_requested = False

        for _ in range(self._max_iterations):
            response = await self._llm.ainvoke(messages)
            raw = getattr(response, "content", str(response))
            if not isinstance(raw, str):
                raw = str(raw)

            tool_call = self._try_parse_tool_call(raw)
            if tool_call is None:
                if (
                    self._is_direct_weather_query(content)
                    and not weather_tool_retry_requested
                    and not self._has_weather_tool_call(tool_calls)
                ):
                    weather_tool_retry_requested = True
                    messages.append({"role": "assistant", "content": raw})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "当前用户是在直接查询天气/温度/降雨。必须先调用 get_weather "
                                "或 get_forecast 获取实时数据；如果缺少城市, 请结合已知旅行上下文推断, "
                                "仍缺失时再追问。只输出工具调用 JSON。"
                            ),
                        }
                    )
                    continue
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
                reused_weather = self._maybe_reuse_weather_result(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    content=content,
                    history=history or [],
                )
                if reused_weather is not None:
                    record = ToolCallRecord(
                        tool=tool_name,
                        input=tool_input,
                        output=reused_weather,
                        status="done",
                        message="复用历史天气信息",
                    )
                    tool_calls.append(record)
                    await self._emit_tool_event(on_tool_event, record)
                    messages.append({"role": "assistant", "content": raw})
                    result_str = json.dumps(reused_weather, ensure_ascii=False, default=str)
                    messages.append(
                        {
                            "role": "user",
                            "content": f"已复用历史天气信息: {result_str[:800]}",
                        }
                    )
                    continue

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
                        "content": (
                            f"工具 {tool_name} 失败: {exc}。这是可降级错误, 不代表本轮任务失败。"
                            "请结合已知旅行上下文继续处理；不要重复追问已知信息。"
                            "如有替代工具（本地景点库、generate_itinerary 等）请继续尝试；"
                            "如果确实无法获取实时数据, 请说明降级原因并给出可执行建议。"
                        ),
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

    @classmethod
    def _build_travel_context_hint(
        cls,
        content: str,
        history: list[dict[str, str]],
    ) -> str:
        """Extract a compact planning context hint from prior user turns.

        The LLM still decides what to do, but this prevents follow-up turns like
        "预算500" from losing the earlier destination and duration.
        """
        user_parts = [
            message["content"]
            for message in history
            if message.get("role") == "user" and message.get("content")
        ]
        user_parts.append(content)
        joined = "\n".join(user_parts)

        destination = cls._extract_destination(joined)
        days = cls._extract_days(joined)
        budget = cls._extract_budget(joined)
        preferences = cls._extract_preferences(joined)

        if not any([destination, days, budget, preferences]):
            return ""

        lines = [
            "已知旅行上下文（从历史和当前消息提取, 后续决策必须优先使用）：",
        ]
        if destination:
            lines.append(f"- 目的地: {destination}")
        if days:
            lines.append(f"- 天数: {days}天")
        if budget:
            lines.append(f"- 预算: {budget}元")
        if preferences:
            lines.append(f"- 偏好: {'、'.join(preferences)}")
        lines.extend(
            [
                "执行规则：",
                "- 当前用户消息可能只是补充预算、人数或偏好, 必须结合历史中的目的地、天数和预算。",
                "- 已知预算、目的地或天数时不要重复追问这些信息；只追问真正缺失且无法合理默认的信息。",
                "- 天气/POI 工具失败属于可降级错误；优先尝试可替代工具或用已有信息继续生成方案。",
                "- 如果已知目的地、预算和天数且用户要规划行程或继续补充约束, 应优先调用 generate_itinerary。",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _extract_destination(text: str) -> str:
        """Best-effort destination extraction for context hints."""
        patterns = [
            r"(?:想去|我要去|去|到|赴|在)([\u4e00-\u9fa5]{2,8})(?:玩|旅游|旅行|游|周末|[一二两三四五六七八九十\d]+(?:天|日)|$)",
            r"(?:规划|安排|生成)([\u4e00-\u9fa5]{2,8})(?:[一二两三四五六七八九十\d]+(?:天|日)游?)",
            r"([\u4e00-\u9fa5]{2,8})(?:周末|[一二两三四五六七八九十\d]+(?:天|日)游?)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                destination = match.group(1).strip()
                destination = re.sub(r"^(?:我|想|要|帮我|给我|计划|规划)", "", destination)
                if 2 <= len(destination) <= 8:
                    return destination
        return ""

    @staticmethod
    def _extract_budget(text: str) -> int:
        """Extract a CNY budget from Chinese user text."""
        match = re.search(r"(\d{2,6})\s*(?:元|块|人民币|rmb|RMB)", text)
        return int(match.group(1)) if match else 0

    @classmethod
    def _extract_days(cls, text: str) -> int:
        """Extract a simple trip duration in days."""
        match = re.search(r"(\d{1,2})\s*(?:天|日)游?", text)
        if match:
            return int(match.group(1))

        match = re.search(r"([一二两三四五六七八九十]{1,3})\s*(?:天|日)游?", text)
        if match:
            return cls._chinese_number_to_int(match.group(1))
        return 0

    @staticmethod
    def _chinese_number_to_int(text: str) -> int:
        """Convert small Chinese numerals used in trip durations."""
        if text == "十":
            return 10
        if text.startswith("十"):
            return 10 + CHINESE_DIGITS.get(text[-1], 0)
        if "十" in text:
            left, _, right = text.partition("十")
            tens = CHINESE_DIGITS.get(left, 1)
            ones = CHINESE_DIGITS.get(right, 0) if right else 0
            return tens * 10 + ones
        return CHINESE_DIGITS.get(text, 0)

    @staticmethod
    def _extract_preferences(text: str) -> list[str]:
        """Extract known preference labels from user text."""
        preferences: list[str] = []
        for preference, keywords in PREFERENCE_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                preferences.append(preference)
        return preferences

    @staticmethod
    def _is_direct_weather_query(content: str) -> bool:
        """Return whether the current user turn is explicitly asking weather."""
        weather_keywords = (
            "天气",
            "温度",
            "气温",
            "下雨",
            "降雨",
            "会不会雨",
            "冷不冷",
            "热不热",
            "晴",
            "多云",
        )
        return any(keyword in content for keyword in weather_keywords)

    @staticmethod
    def _find_recent_weather_context(history: list[dict[str, str]]) -> str:
        """Find a recent assistant weather answer that can be reused."""
        weather_keywords = (
            "天气",
            "温度",
            "气温",
            "下雨",
            "降雨",
            "晴",
            "多云",
            "阴",
            "雨",
            "°C",
            "度",
        )
        for message in reversed(history[-8:]):
            if message.get("role") != "assistant":
                continue
            content = message.get("content", "")
            if any(keyword in content for keyword in weather_keywords):
                return content
        return ""

    @classmethod
    def _maybe_reuse_weather_result(
        cls,
        tool_name: str,
        tool_input: dict[str, Any],
        content: str,
        history: list[dict[str, str]],
    ) -> dict[str, Any] | None:
        """Reuse recent weather for non-weather follow-ups to avoid duplicate calls."""
        if tool_name not in {"get_weather", "get_forecast"}:
            return None
        if cls._is_direct_weather_query(content):
            return None
        if any(keyword in content for keyword in ("刷新", "重新查", "最新", "再查")):
            return None

        weather_context = cls._find_recent_weather_context(history)
        if not weather_context:
            return None

        city = str(tool_input.get("city", "") or "")
        return {
            "reused": True,
            "city": city,
            "summary": weather_context,
        }

    @staticmethod
    def _has_weather_tool_call(tool_calls: list[ToolCallRecord]) -> bool:
        """Return whether the current turn already attempted a weather tool."""
        return any(call.tool in {"get_weather", "get_forecast"} for call in tool_calls)

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


TourAgent = AgentRuntime
