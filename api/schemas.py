"""API request, response, and WebSocket event schemas."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from models.itinerary import Itinerary


class ChatRequest(BaseModel):
    """Request body for starting or continuing a chat session."""

    content: str = Field(min_length=1, description="User travel request")
    user_id: str = Field(default="anonymous", description="User scope for memory and sessions")


class ChatStartResponse(BaseModel):
    """Response returned when a chat session is created."""

    session_id: str


class AuthRequest(BaseModel):
    """Passphrase verification request."""

    passphrase: str = Field(min_length=1, description="访问口令")


class AuthResponse(BaseModel):
    """Passphrase verification response."""

    user_id: str
    valid: bool = True


class SessionSummary(BaseModel):
    """Historical session summary."""

    session_id: str
    title: str
    created_at: str
    updated_at: str
    status: str


class SessionListResponse(BaseModel):
    """Historical session list response."""

    sessions: list[SessionSummary]


class HistoryMessage(BaseModel):
    """Persisted historical chat message."""

    role: str
    content: str
    tool_calls: list[dict[str, Any]] | None = None
    created_at: str


class SessionMessagesResponse(BaseModel):
    """Historical messages for one session."""

    messages: list[HistoryMessage]


class HistoryItinerary(BaseModel):
    """Archived itinerary response item."""

    id: int
    destination: str
    total_cost: int
    created_at: str
    content: Itinerary


class ItineraryListResponse(BaseModel):
    """Archived itinerary list response."""

    itineraries: list[HistoryItinerary]


class UserMessage(BaseModel):
    """用户通过 WebSocket 发送的消息。"""

    content: str = Field(min_length=1, description="用户消息内容")


class ProgressEvent(BaseModel):
    """Agent progress event sent over WebSocket."""

    type: Literal["progress"] = "progress"
    agent: str
    message: str


class ToolCallEvent(BaseModel):
    """Tool call event for frontend transparency."""

    type: Literal["tool_call"] = "tool_call"
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: Literal["running", "done", "error"] = "done"
    message: str = ""


class AgentResultEvent(BaseModel):
    """Agent 回复事件（支持纯文字回复和行程）。"""

    type: Literal["result"] = "result"
    content: str = Field(default="", description="Agent 回复文字")
    itinerary: Itinerary | None = Field(default=None, description="行程（如果有）")
    tool_calls: list[dict[str, Any]] = Field(default_factory=list, description="工具调用记录")
    metrics: dict[str, Any] = Field(default_factory=dict)


class ErrorEvent(BaseModel):
    """Recoverable or terminal error event."""

    type: Literal["error"] = "error"
    message: str
    recoverable: bool = True


class BusyEvent(BaseModel):
    """会话正在执行中, 拒绝新请求。"""

    type: Literal["busy"] = "busy"
    message: str = "正在处理上一个请求, 请稍候"
