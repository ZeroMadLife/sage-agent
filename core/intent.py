"""LLM 意图理解 — 从自然语言提取结构化旅行意图。

替换 Phase 2 的 parse_input 硬编码关键词匹配。
支持任何中国城市, 识别查询类型（plan/nearby/chat）。
"""
import json
import logging
from typing import Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

QueryType = Literal["plan", "nearby", "chat"]

INTENT_SYSTEM_PROMPT = """你是一个旅行需求解析器。从用户的自然语言中提取结构化信息。

输出JSON格式：
{
  "destination": "城市名（如莆田/杭州/千岛湖）, 无目的地时为空字符串",
  "budget_total": 预算金额（整数）, 无预算信息时为0,
  "preferences": ["偏好标签：美食/自然风光/人文古迹/购物"],
  "dates": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "query_type": "plan/nearby/chat",
  "location_hint": "如果用户提到当前位置如'学校附近', 否则空字符串"
}

query_type判断规则：
- plan: 用户要规划行程（"帮我规划""2日游""周末去XX"）
- nearby: 用户找附近的东西（"附近有什么""这边有什么好吃的"）
- chat: 闲聊或追问（"换一个""太贵了""谢谢"）

只输出JSON, 不要其他文字。"""


class IntentResult(BaseModel):
    """结构化旅行意图。"""

    destination: str = Field(default="", description="目的地城市")
    budget_total: int = Field(default=0, description="总预算（元）")
    preferences: list[str] = Field(default_factory=list, description="偏好标签")
    dates: dict[str, str] = Field(default_factory=dict, description="日期范围")
    query_type: QueryType = Field(default="chat", description="查询类型")
    location_hint: str = Field(default="", description="位置提示")


def _fallback_intent() -> IntentResult:
    """LLM 解析失败时的兜底。"""
    return IntentResult(query_type="chat")


async def parse_intent(content: str, llm: ChatOpenAI) -> IntentResult:
    """用 LLM 从自然语言提取结构化旅行意图。

    Args:
        content: 用户原始输入
        llm: ChatOpenAI 实例（DeepSeek）

    Returns:
        IntentResult, LLM 失败时返回 chat 类型的兜底
    """
    try:
        response = await llm.ainvoke([
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ])
        raw = getattr(response, "content", str(response))
        if not isinstance(raw, str):
            raw = str(raw)
        data = json.loads(raw)
        return IntentResult.model_validate(data)
    except Exception as exc:
        logger.warning("意图解析失败, 使用兜底: %s", exc)
        return _fallback_intent()
