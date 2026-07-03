"""REST routes for chat sessions."""

from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request

from api.schemas import (
    AuthRequest,
    AuthResponse,
    ChatRequest,
    ChatStartResponse,
    ItineraryListResponse,
    SessionListResponse,
    SessionMessagesResponse,
)

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


def _session_store(request: Request) -> Any | None:
    """Return the app-level SessionStore if configured."""
    return getattr(request.app.state, "session_store", None)


@router.post("/api/v1/chat")
async def start_chat(payload: ChatRequest, request: Request) -> ChatStartResponse:
    """Create a lightweight chat session."""
    store = _session_store(request)
    if store is None:
        session_id = str(uuid4())
    else:
        session_id = await store.create_session(payload.user_id, title=payload.content[:200])
    SESSIONS[session_id] = SessionState(request=payload)
    return ChatStartResponse(session_id=session_id)


@router.post("/api/v1/auth")
async def verify_passphrase(request: Request, payload: AuthRequest) -> AuthResponse:
    """Verify a passphrase and return the scoped user ID."""
    auth = getattr(request.app.state, "auth", None)
    if auth is None:
        return AuthResponse(user_id="anonymous", valid=True)

    user_id = auth.verify(payload.passphrase)
    if user_id is None:
        return AuthResponse(user_id="", valid=False)
    return AuthResponse(user_id=user_id, valid=True)


@router.get("/api/v1/sessions")
async def list_sessions(
    request: Request,
    user_id: str,
    limit: int = 20,
) -> SessionListResponse:
    """Return historical sessions for one user."""
    store = _session_store(request)
    sessions = [] if store is None else await store.list_sessions(user_id, limit=limit)
    return SessionListResponse(sessions=sessions)


@router.get("/api/v1/sessions/{session_id}/messages")
async def get_session_messages(
    request: Request,
    session_id: str,
) -> SessionMessagesResponse:
    """Return persisted messages for one session."""
    store = _session_store(request)
    messages = [] if store is None else await store.get_session_messages(session_id)
    return SessionMessagesResponse(messages=messages)


@router.get("/api/v1/sessions/{session_id}/itineraries")
async def get_session_itineraries(
    request: Request,
    session_id: str,
) -> ItineraryListResponse:
    """Return archived itineraries for one session."""
    store = _session_store(request)
    itineraries = (
        [] if store is None else await store.list_itineraries(session_id=session_id, user_id=None)
    )
    return ItineraryListResponse(itineraries=itineraries)


@router.get("/api/v1/itineraries")
async def get_user_itineraries(
    request: Request,
    user_id: str,
) -> ItineraryListResponse:
    """Return all archived itineraries for one user."""
    store = _session_store(request)
    itineraries = (
        [] if store is None else await store.list_itineraries(user_id=user_id, session_id=None)
    )
    return ItineraryListResponse(itineraries=itineraries)
