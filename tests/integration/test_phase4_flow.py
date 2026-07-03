"""Phase 4 API integration flow tests."""

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from agents.react_agent import AgentResponse
from api.main import create_app


def _make_agent_stub() -> MagicMock:
    """创建模拟 Agent, chat 返回简单回复。"""
    agent = MagicMock()
    agent.chat = AsyncMock(return_value=AgentResponse(
        content="好的, 已为你规划杭州行程。",
        tool_calls=[],
        itinerary=None,
    ))
    return agent


def test_create_session_then_connect_stream() -> None:
    """创建 session 后连接 WebSocket 发消息, 收到结果事件。"""
    client = TestClient(create_app(agent=_make_agent_stub()))
    response = client.post("/api/v1/chat", json={"content": "周末去杭州2日游预算500元"})
    session_id = response.json()["session_id"]

    with client.websocket_connect(f"/api/v1/chat/{session_id}/stream") as websocket:
        websocket.send_json({"content": "周末去杭州2日游预算500元"})
        progress = websocket.receive_json()
        result = websocket.receive_json()

    assert progress["type"] == "progress"
    assert result["type"] == "result"
    assert "杭州" in result["content"]
