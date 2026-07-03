"""REST routes for chat sessions."""

from uuid import uuid4

from fastapi import APIRouter

from api.schemas import ChatRequest, ChatStartResponse

router = APIRouter()


class SessionState:
    """一个聊天会话的运行时状态。"""

    def __init__(self, request: ChatRequest) -> None:
        self.request = request
        self.is_executing = False
        self.messages: list[dict[str, str]] = []


SESSIONS: dict[str, SessionState] = {}


@router.get("/health")
async def health() -> dict[str, str]:
    """Health check for local and deployment probes."""
    return {"status": "ok"}


@router.post("/api/v1/chat")
async def start_chat(request: ChatRequest) -> ChatStartResponse:
    """Create a lightweight chat session."""
    session_id = str(uuid4())
    SESSIONS[session_id] = SessionState(request=request)
    return ChatStartResponse(session_id=session_id)
