"""WebSocket endpoints for AI planning progress."""

from typing import Any

from fastapi import APIRouter, WebSocket

from api.routes import SESSION_INPUTS
from api.schemas import ErrorEvent
from api.services.chat_runner import run_chat

router = APIRouter()


def _graph_from_websocket(websocket: WebSocket) -> Any | None:
    """Read the configured LangGraph app from FastAPI state."""
    app = websocket.scope.get("app")
    state = getattr(app, "state", None)
    return getattr(state, "graph", None)


@router.websocket("/api/v1/chat/{session_id}/stream")
async def chat_stream(websocket: WebSocket, session_id: str) -> None:
    """Stream chat events for a known session."""
    await websocket.accept()
    request = SESSION_INPUTS.get(session_id)
    if request is None:
        await websocket.send_json(ErrorEvent(message=f"Unknown session: {session_id}").model_dump())
        await websocket.close()
        return

    graph = _graph_from_websocket(websocket)
    if graph is None:
        await websocket.send_json(ErrorEvent(message="Graph is not configured").model_dump())
        await websocket.close()
        return

    async for event in run_chat(
        graph=graph,
        user_id=request.user_id,
        session_id=session_id,
        content=request.content,
    ):
        await websocket.send_json(event.model_dump())
    await websocket.close()
