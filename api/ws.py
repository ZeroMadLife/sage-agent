"""WebSocket endpoint for multi-turn agent chat."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, WebSocket, WebSocketException, status
from starlette.requests import HTTPConnection

from api.routes import SESSIONS
from api.schemas import BusyEvent, ErrorEvent, UserMessage
from api.services.chat_runner import run_agent_chat

logger = logging.getLogger(__name__)

async def _reject_legacy_chat_in_production(connection: HTTPConnection) -> None:
    app_env = str(getattr(connection.app.state, "cloud_app_env", "development")).lower()
    if app_env == "production":
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="legacy chat is unavailable in production",
        )


router = APIRouter(dependencies=[Depends(_reject_legacy_chat_in_production)])


@router.websocket("/api/v1/chat/{session_id}/stream")
async def chat_stream(websocket: WebSocket, session_id: str) -> None:
    """Multi-turn agent chat over WebSocket long connection.

    连接保持打开, 循环接收用户消息并流式返回事件。
    支持防重入：同一 session 执行中时拒绝新请求。
    """
    await websocket.accept()

    session = SESSIONS.get(session_id)
    if session is None:
        await websocket.send_json(ErrorEvent(message=f"Unknown session: {session_id}").model_dump())
        await websocket.close()
        return

    agent: Any = getattr(websocket.app.state, "agent", None)
    if agent is None:
        await websocket.send_json(ErrorEvent(message="Agent is not configured").model_dump())
        await websocket.close()
        return

    # 长连接循环：等待消息 → 执行 → 返回事件 → 等下一条消息
    while True:
        try:
            raw = await websocket.receive_json()
        except Exception:
            # 连接关闭或出错
            break

        try:
            msg = UserMessage(**raw)
        except Exception as exc:
            await websocket.send_json(ErrorEvent(message=f"Invalid message: {exc}").model_dump())
            continue

        # 防重入
        if session.is_executing:
            await websocket.send_json(BusyEvent().model_dump())
            continue

        session.is_executing = True
        try:
            store = getattr(websocket.app.state, "session_store", None)
            history = (
                await store.load_messages(session_id) if store is not None else session.messages
            )
            async for event in run_agent_chat(
                agent=agent,
                content=msg.content,
                user_id=session.request.user_id,
                session_id=session_id,
                history=history,
                session_store=store,
            ):
                await websocket.send_json(event.model_dump())
                if store is None and event.type == "result":
                    session.messages.append({"role": "user", "content": msg.content})
                    session.messages.append({"role": "assistant", "content": event.content})
        except Exception as exc:
            logger.exception("Agent chat failed")
            await websocket.send_json(ErrorEvent(message=f"Agent error: {exc}").model_dump())
        finally:
            session.is_executing = False
