"""Chat runner tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from agents.react_agent import AgentResponse, ToolCallRecord, TourAgent
from api.schemas import AgentResultEvent, ProgressEvent
from api.services.chat_runner import run_agent_chat
from models.itinerary import Itinerary


def _make_agent(response: AgentResponse) -> MagicMock:
    agent = MagicMock()
    agent.chat = AsyncMock(return_value=response)
    return agent


async def test_run_agent_chat_emits_progress_and_result() -> None:
    """run_agent_chat 返回 progress 和 result 事件。"""
    agent = _make_agent(
        AgentResponse(
            content="杭州现在28度, 晴天。",
            tool_calls=[],
            itinerary=None,
        )
    )

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
    agent = _make_agent(
        AgentResponse(
            content="附近有沙县小吃。",
            tool_calls=[
                ToolCallRecord(
                    tool="search_nearby",
                    input={"location": "120.1,30.2", "keywords": "餐饮"},
                    output=[{"name": "沙县小吃"}],
                ),
            ],
            itinerary=None,
        )
    )

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


async def test_run_agent_chat_streams_tool_running_before_tool_finishes() -> None:
    """工具执行期间应先流式推送 running 事件, 而不是等 Agent 完整结束。"""
    finish_tool = asyncio.Event()

    async def get_forecast(city: str, days: int = 7) -> list[dict[str, object]]:
        await finish_tool.wait()
        return [{"city": city, "days": days}]

    llm = MagicMock()
    llm.ainvoke = AsyncMock(
        side_effect=[
            MagicMock(content='{"action": "get_forecast", "input": {"city": "杭州", "days": 7}}'),
            MagicMock(content="杭州周末天气晴朗，适合出行。"),
        ]
    )
    agent = TourAgent(llm=llm, tools={"get_forecast": get_forecast})
    stream = run_agent_chat(
        agent=agent,
        content="杭州周末天气",
        user_id="anonymous",
        session_id="session-001",
    )

    progress = await anext(stream)
    running_task = asyncio.create_task(anext(stream))
    running = await asyncio.wait_for(running_task, timeout=0.2)

    assert progress.type == "progress"
    assert running.type == "tool_call"
    assert running.tool == "get_forecast"
    assert running.status == "running"

    finish_tool.set()
    remaining = [event async for event in stream]
    assert any(event.type == "tool_call" and event.status == "done" for event in remaining)
    assert remaining[-1].type == "result"


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


async def test_run_agent_chat_persists_messages_and_itinerary() -> None:
    """When a SessionStore is provided, runner should save both sides and archive plans."""
    itinerary = {
        "destination": "杭州",
        "days": [],
        "total_cost": 200,
        "weather_summary": "晴",
    }
    agent = _make_agent(
        AgentResponse(
            content="杭州行程已生成。",
            tool_calls=[ToolCallRecord(tool="generate_itinerary", input={}, output=itinerary)],
            itinerary=itinerary,
        )
    )
    store = MagicMock()
    store.save_message = AsyncMock()
    store.archive_itinerary = AsyncMock()
    store.maybe_compress = AsyncMock(return_value=False)

    events = [
        event
        async for event in run_agent_chat(
            agent=agent,
            content="帮我规划杭州2日游",
            user_id="u_1",
            session_id="session-001",
            session_store=store,
        )
    ]

    assert events[-1].type == "result"
    store.save_message.assert_any_await("session-001", "user", "帮我规划杭州2日游")
    store.save_message.assert_any_await(
        "session-001",
        "assistant",
        "杭州行程已生成。",
        tool_calls=[{"tool": "generate_itinerary", "error": ""}],
    )
    store.archive_itinerary.assert_awaited_once()
    archived = store.archive_itinerary.await_args.args[2]
    assert isinstance(archived, Itinerary)
    store.maybe_compress.assert_awaited_once_with("session-001")
