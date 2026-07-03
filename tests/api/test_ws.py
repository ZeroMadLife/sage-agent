"""WebSocket route tests."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from agents.react_agent import AgentResponse
from api.main import create_app


def _make_agent_stub(content: str = "你好！我是TourSwarm。") -> MagicMock:
    """创建一个模拟 Agent, chat 返回指定内容。"""
    agent = MagicMock()
    agent.chat = AsyncMock(return_value=AgentResponse(
        content=content,
        tool_calls=[],
        itinerary=None,
    ))
    return agent


def test_chat_websocket_rejects_unknown_session() -> None:
    """未知 session 返回 error 并关闭。"""
    client = TestClient(create_app(agent=_make_agent_stub()))

    with client.websocket_connect("/api/v1/chat/missing/stream") as websocket:
        event = websocket.receive_json()

    assert event["type"] == "error"
    assert "Unknown session" in event["message"]


def test_chat_websocket_streams_agent_result() -> None:
    """WebSocket 返回 progress + result 事件。"""
    agent = _make_agent_stub(content="杭州现在28度, 晴天。")
    client = TestClient(create_app(agent=agent))
    response = client.post("/api/v1/chat", json={"content": "杭州天气"})
    session_id = response.json()["session_id"]

    with client.websocket_connect(f"/api/v1/chat/{session_id}/stream") as websocket:
        # 发送用户消息
        websocket.send_json({"content": "杭州天气"})
        # 接收事件
        progress = websocket.receive_json()
        result = websocket.receive_json()

    assert progress["type"] == "progress"
    assert result["type"] == "result"
    assert "28度" in result["content"]


def test_chat_websocket_rejects_concurrent_request() -> None:
    """执行中时发送第二条消息应返回 busy。"""
    # 创建一个慢 Agent
    agent = MagicMock()

    async def slow_chat(**kwargs: Any) -> AgentResponse:
        import asyncio
        await asyncio.sleep(0.5)
        return AgentResponse(content="完成", tool_calls=[], itinerary=None)

    agent.chat = slow_chat
    client = TestClient(create_app(agent=agent))
    response = client.post("/api/v1/chat", json={"content": "杭州2日游"})
    session_id = response.json()["session_id"]

    with client.websocket_connect(f"/api/v1/chat/{session_id}/stream") as websocket:
        websocket.send_json({"content": "第一个问题"})
        # 等待执行完成
        websocket.receive_json()  # progress
        websocket.receive_json()  # result

        # 再发一条, 应该正常处理（上一条已完成）
        websocket.send_json({"content": "第二个问题"})
        websocket.receive_json()  # progress
        result2 = websocket.receive_json()
        assert result2["type"] == "result"


def test_chat_websocket_agent_error_returns_error_event() -> None:
    """Agent 执行失败时返回 error 事件。"""
    agent = MagicMock()
    agent.chat = AsyncMock(side_effect=Exception("LLM超时"))
    client = TestClient(create_app(agent=agent))
    response = client.post("/api/v1/chat", json={"content": "你好"})
    session_id = response.json()["session_id"]

    with client.websocket_connect(f"/api/v1/chat/{session_id}/stream") as websocket:
        websocket.send_json({"content": "你好"})
        progress = websocket.receive_json()
        error = websocket.receive_json()

    assert progress["type"] == "progress"
    assert error["type"] == "error"
    assert "LLM超时" in error["message"]
