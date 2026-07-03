"""ReAct Agent 测试。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.react_agent import AgentResponse, TourAgent


@pytest.fixture
def mock_llm() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_tools() -> dict:
    nearby_result = [{"name": "沙县小吃", "location": "120.1,30.2", "rating": "4.2"}]
    weather_result = {"temp_c": 28, "text": "晴"}
    forecast_result = [{"date": "2026-07-05", "text_day": "晴"}]
    itinerary_result = {
        "destination": "杭州",
        "days": [],
        "total_cost": 200,
        "weather_summary": "晴",
        "weather": {"current": {"temp_c": 28}, "error": False},
    }
    return {
        "search_nearby": MagicMock(return_value=nearby_result),
        "get_weather": MagicMock(return_value=weather_result),
        "get_forecast": AsyncMock(return_value=forecast_result),
        "generate_itinerary": AsyncMock(return_value=itinerary_result),
    }


async def test_agent_handles_nearby_query(mock_llm: MagicMock, mock_tools: dict) -> None:
    """附近查询调 search_nearby 工具。"""
    mock_llm.ainvoke = AsyncMock(
        side_effect=[
            MagicMock(
                content='{"action": "search_nearby", "input": {"location": "120.1,30.2", "keywords": "餐饮"}}'
            ),
            MagicMock(content="附近有一家沙县小吃, 评分4.2, 人均15元左右。"),
        ]
    )
    agent = TourAgent(llm=mock_llm, tools=mock_tools)
    response = await agent.chat("附近有什么好吃的", user_id="u1", session_id="s1")

    assert isinstance(response, AgentResponse)
    assert "沙县小吃" in response.content
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].tool == "search_nearby"


async def test_agent_handles_chat_without_tools(mock_llm: MagicMock, mock_tools: dict) -> None:
    """闲聊直接回复不调工具。"""
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(
            content="你好！我是TourSwarm穷游助手, 可以帮你查附近美食、规划行程。"
        )
    )
    agent = TourAgent(llm=mock_llm, tools=mock_tools)
    response = await agent.chat("你好", user_id="u1", session_id="s1")

    assert "TourSwarm" in response.content
    assert len(response.tool_calls) == 0


async def test_agent_calls_generate_itinerary_for_complex_plan(
    mock_llm: MagicMock, mock_tools: dict
) -> None:
    """复杂规划调 generate_itinerary（内部多Agent协作）。"""
    mock_llm.ainvoke = AsyncMock(
        side_effect=[
            MagicMock(
                content='{"action": "generate_itinerary", "input": {"destination": "杭州", "budget_total": 500, "preferences": ["美食"], "dates": {"start": "2026-07-05", "end": "2026-07-06"}}}'
            ),
            MagicMock(content="好的！我为你规划了杭州2日游行程, 总花费200元, 天气晴朗适合出行。"),
        ]
    )
    agent = TourAgent(llm=mock_llm, tools=mock_tools)
    response = await agent.chat("帮我规划杭州2日游500元", user_id="u1", session_id="s1")

    assert response.tool_calls[0].tool == "generate_itinerary"
    assert response.itinerary is not None
    assert response.itinerary["destination"] == "杭州"


async def test_agent_extracts_tool_call_when_llm_wraps_json_in_prose(
    mock_llm: MagicMock, mock_tools: dict
) -> None:
    """LLM 在工具 JSON 前后输出解释文字时, Agent 仍应执行工具而不是展示 JSON。"""
    mock_llm.ainvoke = AsyncMock(
        side_effect=[
            MagicMock(
                content='好的，我先查一下杭州周末天气。\n{"action": "get_forecast", "input": {"city": "杭州", "days": 7}}'
            ),
            MagicMock(content="杭州周末天气晴朗，适合安排西湖和河坊街。"),
        ]
    )
    agent = TourAgent(llm=mock_llm, tools=mock_tools)

    response = await agent.chat("我要周末去杭州旅游两天", user_id="u1", session_id="s1")

    assert response.tool_calls[0].tool == "get_forecast"
    assert "action" not in response.content
    assert "get_forecast" not in response.content


async def test_agent_emits_tool_running_and_done_events(
    mock_llm: MagicMock, mock_tools: dict
) -> None:
    """工具执行前后应产生事件, 让 WebSocket 可以实时展示工具状态。"""
    mock_llm.ainvoke = AsyncMock(
        side_effect=[
            MagicMock(content='{"action": "get_forecast", "input": {"city": "杭州", "days": 7}}'),
            MagicMock(content="杭州周末天气晴朗，适合出行。"),
        ]
    )
    seen = []

    async def on_tool_event(record) -> None:
        seen.append(record)

    agent = TourAgent(llm=mock_llm, tools=mock_tools)
    await agent.chat(
        "杭州周末天气",
        user_id="u1",
        session_id="s1",
        on_tool_event=on_tool_event,
    )

    assert [event.status for event in seen] == ["running", "done"]
    assert seen[0].tool == "get_forecast"
    assert seen[0].message.startswith("正在")


async def test_agent_handles_tool_error(mock_llm: MagicMock, mock_tools: dict) -> None:
    """工具失败时Agent能告知用户。"""
    mock_tools["get_weather"] = MagicMock(side_effect=Exception("API超时"))
    mock_llm.ainvoke = AsyncMock(
        side_effect=[
            MagicMock(content='{"action": "get_weather", "input": {"city": "杭州"}}'),
            MagicMock(content="抱歉, 天气查询暂时不可用, 建议出行前查看天气预报。"),
        ]
    )
    agent = TourAgent(llm=mock_llm, tools=mock_tools)
    response = await agent.chat("杭州天气", user_id="u1", session_id="s1")

    assert "不可用" in response.content
    assert response.tool_calls[0].error != ""


async def test_agent_uses_history_for_followup(mock_llm: MagicMock, mock_tools: dict) -> None:
    """追问时基于对话历史调整。"""
    history = [
        {"role": "user", "content": "帮我规划杭州2日游"},
        {"role": "assistant", "content": "好的, 已为你规划杭州行程..."},
    ]
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(content="好的, 我把第一天的景点减少一个, 调整后的行程更轻松。")
    )
    agent = TourAgent(llm=mock_llm, tools=mock_tools)
    response = await agent.chat("第一天景点太多了", user_id="u1", session_id="s1", history=history)

    assert "调整" in response.content
    # 验证 history 被传入 messages
    call_messages = mock_llm.ainvoke.call_args.args[0]
    assert any("杭州" in m.get("content", "") for m in call_messages)
