"""Run the ReAct Agent and emit UI-friendly events."""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from agents.react_agent import ToolCallRecord
from api.schemas import AgentResultEvent, ErrorEvent, ProgressEvent, ToolCallEvent


async def run_agent_chat(
    agent: Any,
    content: str,
    user_id: str,
    session_id: str,
    history: list[dict[str, str]] | None = None,
) -> AsyncIterator[ProgressEvent | ToolCallEvent | AgentResultEvent | ErrorEvent]:
    """执行一次 Agent 对话, 流式返回事件。

    Args:
        agent: TourAgent 实例
        content: 用户消息
        user_id: 用户ID
        session_id: 会话ID
        history: 对话历史（多轮上下文）

    Yields:
        ProgressEvent → 工具调用前
        ToolCallEvent → 每次工具调用
        AgentResultEvent → 最终回复
        ErrorEvent → 错误
    """
    started = time.perf_counter()

    yield ProgressEvent(agent="agent", message="正在思考...")

    tool_events: asyncio.Queue[ToolCallRecord] = asyncio.Queue()

    async def on_tool_event(record: ToolCallRecord) -> None:
        await tool_events.put(record)

    async def _call_agent() -> Any:
        try:
            return await agent.chat(
                content=content,
                user_id=user_id,
                session_id=session_id,
                history=history or [],
                on_tool_event=on_tool_event,
            )
        except TypeError as exc:
            if "on_tool_event" not in str(exc):
                raise
            return await agent.chat(
                content=content,
                user_id=user_id,
                session_id=session_id,
                history=history or [],
            )

    streamed_tool_events = 0
    try:
        agent_task = asyncio.create_task(_call_agent())
        while not agent_task.done() or not tool_events.empty():
            if tool_events.empty():
                try:
                    record = await asyncio.wait_for(tool_events.get(), timeout=0.05)
                except TimeoutError:
                    continue
            else:
                record = tool_events.get_nowait()

            streamed_tool_events += 1
            yield ToolCallEvent(
                tool=record.tool,
                args=record.input,
                status=record.status,
                message=record.message,
            )

        response = await agent_task
    except Exception as exc:
        yield ErrorEvent(message=f"Agent 执行失败: {exc}", recoverable=True)
        return

    if streamed_tool_events == 0:
        for tc in response.tool_calls:
            yield ToolCallEvent(
                tool=tc.tool,
                args=tc.input,
                status=tc.status,
                message=tc.message,
            )

    latency_ms = int((time.perf_counter() - started) * 1000)

    # 如果有行程, 转换为 Itinerary 对象
    itinerary_obj = None
    if response.itinerary:
        from models.itinerary import Itinerary

        try:
            itinerary_obj = Itinerary.model_validate(response.itinerary)
        except Exception:
            itinerary_obj = None

    yield AgentResultEvent(
        content=response.content,
        itinerary=itinerary_obj,
        tool_calls=[{"tool": tc.tool, "error": tc.error} for tc in response.tool_calls],
        metrics={"latency_ms": latency_ms},
    )
