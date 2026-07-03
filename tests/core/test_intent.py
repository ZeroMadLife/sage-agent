"""LLM 意图理解测试。"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.intent import parse_intent


@pytest.fixture
def mock_llm() -> MagicMock:
    return MagicMock()


async def test_parse_intent_extracts_putian(mock_llm: MagicMock) -> None:
    """能识别不在硬编码列表中的城市（莆田）。"""
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(
        content=json.dumps({
            "destination": "莆田", "budget_total": 500,
            "preferences": ["美食"],
            "dates": {"start": "2026-07-12", "end": "2026-07-13"},
            "query_type": "plan",
        }, ensure_ascii=False)
    ))
    result = await parse_intent("莆田周末两日游500块喜欢海鲜", mock_llm)
    assert result.destination == "莆田"
    assert result.query_type == "plan"


async def test_parse_intent_detects_nearby_query(mock_llm: MagicMock) -> None:
    """识别附近查询类型。"""
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(
        content=json.dumps({
            "destination": "", "budget_total": 0, "preferences": ["美食"],
            "dates": {}, "query_type": "nearby", "location_hint": "学校附近",
        }, ensure_ascii=False)
    ))
    result = await parse_intent("学校附近有什么好吃的", mock_llm)
    assert result.query_type == "nearby"


async def test_parse_intent_handles_llm_error(mock_llm: MagicMock) -> None:
    """LLM返回无效JSON时fallback到chat。"""
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(content="not json"))
    result = await parse_intent("随便", mock_llm)
    assert result.query_type == "chat"
    assert result.destination == ""


async def test_parse_intent_detects_chat(mock_llm: MagicMock) -> None:
    """识别闲聊类型。"""
    mock_llm.ainvoke = AsyncMock(return_value=MagicMock(
        content=json.dumps({
            "destination": "", "budget_total": 0, "preferences": [],
            "dates": {}, "query_type": "chat",
        }, ensure_ascii=False)
    ))
    result = await parse_intent("你好", mock_llm)
    assert result.query_type == "chat"
