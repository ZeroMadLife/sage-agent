"""Chat runner tests."""

from unittest.mock import AsyncMock, MagicMock

from agents.react_agent import AgentResponse, ToolCallRecord
from api.schemas import AgentResultEvent, ProgressEvent
from api.services.chat_runner import run_agent_chat


def _make_agent(response: AgentResponse) -> MagicMock:
    agent = MagicMock()
    agent.chat = AsyncMock(return_value=response)
    return agent


async def test_run_agent_chat_emits_progress_and_result() -> None:
    """run_agent_chat 返回 progress 和 result 事件。"""
    agent = _make_agent(AgentResponse(
        content="杭州现在28度, 晴天。",
        tool_calls=[],
        itinerary=None,
    ))

    events = [
        event
        async for event in run_agent_chat(
            agent=agent,
            content="杭州天气",
            user_id="anonymous",
            session_id="session-001",
        )
    ]

    assert isinstance(events[0], ProgressEvent)
    assert events[0].agent == "agent"
    assert isinstance(events[-1], AgentResultEvent)
    assert "28度" in events[-1].content


async def test_run_agent_chat_emits_tool_call_events() -> None:
    """工具调用产生 ToolCallEvent。"""
    agent = _make_agent(AgentResponse(
        content="附近有沙县小吃。",
        tool_calls=[
            ToolCallRecord(
                tool="search_nearby",
                input={"location": "120.1,30.2", "keywords": "餐饮"},
                output=[{"name": "沙县小吃"}],
            ),
        ],
        itinerary=None,
    ))

    events = [
        event
        async for event in run_agent_chat(
            agent=agent,
            content="附近有什么好吃的",
            user_id="anonymous",
            session_id="session-001",
        )
    ]

    # progress, tool_call, result
    assert len(events) == 3
    assert events[0].type == "progress"
    assert events[1].type == "tool_call"
    assert events[1].tool == "search_nearby"
    assert events[2].type == "result"
    assert "沙县" in events[2].content


async def test_run_agent_chat_handles_agent_error() -> None:
    """Agent 抛异常时返回 ErrorEvent。"""
    agent = MagicMock()
    agent.chat = AsyncMock(side_effect=Exception("LLM超时"))

    events = [
        event
        async for event in run_agent_chat(
            agent=agent,
            content="你好",
            user_id="anonymous",
            session_id="session-001",
        )
    ]

    assert events[0].type == "progress"
    assert events[1].type == "error"
    assert "LLM超时" in events[1].message
