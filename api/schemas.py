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


class CodingSessionRequest(BaseModel):
    """Request body for creating a coding session."""

    workspace_root: str | None = Field(default=None, description="Workspace root override")


class CodingSessionResponse(BaseModel):
    """Response returned when a coding session is created."""

    session_id: str
    workspace_root: str


class CodingFileEntry(BaseModel):
    """One entry in a coding file tree listing."""

    name: str
    is_dir: bool


class CodingFilesResponse(BaseModel):
    """File tree listing for a coding session."""

    path: str
    entries: list[CodingFileEntry]


class CodingFileContentResponse(BaseModel):
    """File content preview for a coding session."""

    path: str
    content: str
    lines: int


class CodingGitStatusResponse(BaseModel):
    """Git status for a coding workspace."""

    is_git: bool
    branch: str = ""
    dirty_count: int = 0
    changed_files: list[str] = Field(default_factory=list)


class CodingModel(BaseModel):
    """One selectable model."""

    id: str
    label: str
    provider: str


class CodingModelsResponse(BaseModel):
    """Available models for a coding session."""

    models: list[CodingModel]
    current: str | None = None


class CodingModelSwitchRequest(BaseModel):
    """Request body for switching a session's model."""

    model_id: str = Field(min_length=1)


class CodingSkillSummary(BaseModel):
    """Skill list entry."""

    name: str
    description: str
    source: str
    argument_hint: str = ""


class CodingSkillsResponse(BaseModel):
    """Skill list response."""

    skills: list[CodingSkillSummary]


class CodingSkillDetailResponse(BaseModel):
    """Skill content preview."""

    name: str
    description: str
    source: str
    content: str


class CodingMcpServer(BaseModel):
    """MCP server config entry."""

    name: str
    transport: str
    status: str = "configured"


class CodingMcpServersResponse(BaseModel):
    """MCP server config listing."""

    servers: list[CodingMcpServer]


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
