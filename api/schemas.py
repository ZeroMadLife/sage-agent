"""API request, response, and WebSocket event schemas."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
    approval_policy: Literal["auto", "ask", "never"] = "auto"


class CodingSessionResponse(BaseModel):
    """Response returned when a coding session is created."""

    session_id: str
    workspace_root: str
    permission_mode: Literal["default", "accept_edits", "auto", "plan"] = "default"


class CodingSessionSummary(BaseModel):
    """One local coding-agent session shown in the Sage workbench."""

    session_id: str
    title: str
    workspace_root: str
    created_at: str = ""
    updated_at: str = ""
    runtime_mode: str = "default"
    message_count: int = 0
    pinned: bool = False
    archived: bool = False


class CodingSessionMetadataRequest(BaseModel):
    """User-visible metadata changes for a coding session."""

    title: str | None = Field(default=None, min_length=1, max_length=120)
    pinned: bool | None = None
    archived: bool | None = None


class CodingSessionsResponse(BaseModel):
    """Local coding-agent session history."""

    sessions: list[CodingSessionSummary]


class CodingSessionMessage(BaseModel):
    """One replayable coding-agent chat message."""

    role: Literal["user", "assistant"]
    content: str
    created_at: str = ""


class CodingSessionMessagesResponse(BaseModel):
    """Replayable chat messages for one local coding-agent session."""

    messages: list[CodingSessionMessage]


class CodingTimelineEvent(BaseModel):
    """One durable browser-visible coding event."""

    event_id: str
    session_id: str
    run_id: str
    sequence: int
    kind: str
    status: str
    timestamp: str
    payload: dict[str, Any]


class CodingActiveRun(BaseModel):
    """The session run currently owned by the server."""

    run_id: str
    status: Literal["running"] = "running"


class CodingTimelineResponse(BaseModel):
    """Cursor-paginated durable coding timeline."""

    items: list[CodingTimelineEvent]
    next_cursor: int
    has_more: bool
    older_cursor: int | None = None
    latest_cursor: int = 0
    active_run: CodingActiveRun | None = None


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
    context_configured: bool = False
    context_window_tokens: int | None = None
    output_reserve_tokens: int | None = None
    reasoning_modes: list[str] = Field(default_factory=list)


class CodingModelsResponse(BaseModel):
    """Available models for a coding session."""

    models: list[CodingModel]
    current: str | None = None
    reasoning_mode: Literal["off", "low", "medium", "high"] = "off"


class CodingModelSwitchRequest(BaseModel):
    """Request body for switching a session's model."""

    model_id: str = Field(min_length=1)


class CodingReasoningSwitchRequest(BaseModel):
    """Select a server-declared reasoning mode for a coding session."""

    mode: Literal["off", "low", "medium", "high"] = "off"


class CodingProviderReasoningInput(BaseModel):
    """Non-secret model reasoning descriptor submitted by the settings editor."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal[
        "unsupported", "openai_reasoning_effort", "anthropic_thinking_budget"
    ]
    modes: list[Literal["low", "medium", "high"]] | None = None
    budgets: dict[Literal["low", "medium", "high"], int] | None = None


class CodingProviderModelInput(BaseModel):
    """One declared model in a non-secret provider settings document."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=192)
    label: str = Field(min_length=1, max_length=512)
    context_window_tokens: int | None = Field(default=None, gt=0)
    output_reserve_tokens: int | None = Field(default=None, gt=0)
    reasoning: CodingProviderReasoningInput | None = None


class CodingProviderInput(BaseModel):
    """One non-secret provider declaration submitted by the settings editor."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=512)
    api_mode: Literal["openai_chat_completions", "anthropic_messages"]
    base_url: str = Field(min_length=1, max_length=512)
    api_key_env: str = Field(min_length=1, max_length=128)
    models: list[CodingProviderModelInput] = Field(min_length=1, max_length=256)


class CodingProviderSettingsUpdate(BaseModel):
    """Strict body accepted by the project-local provider settings API."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    default_model: str = Field(min_length=1, max_length=192)
    providers: list[CodingProviderInput] = Field(min_length=1, max_length=32)


class CodingProviderModelResponse(BaseModel):
    """A sanitized provider model entry returned to the browser."""

    id: str
    label: str
    context_window_tokens: int | None = None
    output_reserve_tokens: int | None = None
    reasoning: dict[str, Any]


class CodingProviderResponse(BaseModel):
    """A sanitized provider entry with only key-availability state."""

    id: str
    label: str
    api_mode: Literal["openai_chat_completions", "anthropic_messages"]
    base_url: str
    api_key_env: str
    api_key_configured: bool
    models: list[CodingProviderModelResponse]


class CodingProviderSettingsResponse(BaseModel):
    """Project-local provider settings safe for browser consumption."""

    version: Literal[1]
    default_model: str
    source: Literal["legacy_toml", "project_json", "deployment_json"]
    editable: bool
    providers: list[CodingProviderResponse]


class CodingUsageModelAggregate(BaseModel):
    """Token totals grouped by concrete model id."""

    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    total_tokens: int | None = None


class CodingUsageDailyAggregate(BaseModel):
    """Token totals grouped by UTC date."""

    date: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    total_tokens: int | None = None


class CodingUsageSummary(BaseModel):
    """Provider-reported token totals without prompt or response content."""

    range_days: int
    request_count: int
    session_count: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cache_hit_ratio: float | None = None
    cost: float | None = None
    models: list[CodingUsageModelAggregate]
    daily: list[CodingUsageDailyAggregate]


class CodingContextCompactRequest(BaseModel):
    """Request body for an explicit context compaction."""

    focus: str = Field(default="", max_length=4000)


class CodingContextSnapshot(BaseModel):
    """Current context budget and durable compaction state."""

    configured: bool
    model_id: str | None = None
    model_limit_tokens: int | None = None
    output_reserve_tokens: int | None = None
    effective_limit_tokens: int | None = None
    used_tokens: int | None = None
    usage_ratio: float | None = None
    level: str
    estimated: bool | None = None
    compactable: bool
    active_run_id: str | None = None
    context_operation_active: bool = False
    checkpoint_id: str | None = None
    resume_status: str
    checkpoint_resume_enabled: bool
    latest_attempt: dict[str, Any] | None = None
    stale_started: bool = False


class CodingContextCompactResponse(BaseModel):
    """Result of an explicit context compaction."""

    compaction_id: str
    applied: bool
    before_tokens: int
    after_tokens: int
    archived_items: int
    reason: str = ""
    retryable: bool = False
    context: CodingContextSnapshot


class CodingRunStopRequest(BaseModel):
    """Run-bound cancellation request guarded against stale clients."""

    run_id: str = Field(min_length=1)


class PermissionModeSwitchRequest(BaseModel):
    """Request body for switching a session's permission mode."""

    mode: Literal["default", "accept_edits", "auto", "plan"]


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


class CodingApprovalResponse(BaseModel):
    """Pending approval returned to the coding UI."""

    approval_id: str
    session_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    description: str
    pattern_key: str


class CodingApprovalRespondRequest(BaseModel):
    """Request body for resolving one approval."""

    approval_id: str = Field(min_length=1)
    choice: Literal["once", "session", "always", "deny"]


class CodingMemoryCandidate(BaseModel):
    """One evidence-backed candidate in a memory proposal."""

    content: str
    topic: str
    source: str
    source_ref: str = ""
    created_at: str = ""


class CodingMemoryEvent(BaseModel):
    """One persisted memory proposal lifecycle event."""

    event_id: str
    event_type: str
    proposal_id: str
    workspace_id: str
    session_id: str = ""
    run_id: str = ""
    reflection_id: str = ""
    candidate_count: int = Field(default=0, ge=0)
    base_revision: int = Field(default=0, ge=0)
    revision: int = Field(default=0, ge=0)
    created_at: str = ""


class CodingMemoryProposal(BaseModel):
    """One persisted memory proposal returned to the review UI."""

    proposal_id: str
    workspace_id: str
    session_id: str = ""
    run_id: str = ""
    reflection_id: str = ""
    status: Literal["pending", "approved", "rejected"]
    projection_status: Literal["pending", "complete"]
    revision: int = Field(ge=0)
    base_revision: int = Field(default=0, ge=0)
    candidate_count: int = Field(ge=0)
    candidates: list[CodingMemoryCandidate]
    created_at: str = ""
    updated_at: str = ""


class CodingMemoryProposalsResponse(BaseModel):
    """Session-scoped memory proposal listing."""

    proposals: list[CodingMemoryProposal]


class CodingMemoryProposalDetail(BaseModel):
    """A proposal plus its persisted lifecycle evidence."""

    proposal: CodingMemoryProposal
    events: list[CodingMemoryEvent]


class CodingMemoryProposalTransitionRequest(BaseModel):
    """Revision guard for an ID-addressed proposal transition."""

    expected_revision: int = Field(ge=0)


class CodingMemoryProposalDecisionRequest(CodingMemoryProposalTransitionRequest):
    """Legacy collection-style transition request with an explicit ID."""

    proposal_id: str = Field(min_length=1, max_length=128)


class CodingRunSummary(BaseModel):
    """One coding run summary for the workbench run history."""

    run_id: str
    status: str
    event_count: int
    tool_count: int
    error_count: int
    last_event_type: str
    started_at: str = ""
    updated_at: str = ""
    changed_files: list[str] = Field(default_factory=list)


class CodingRunsResponse(BaseModel):
    """Run history for one coding session."""

    runs: list[CodingRunSummary]


class CodingRunTimelineEntry(BaseModel):
    """One UI-ready worklog entry derived from raw run trace."""

    kind: str
    title: str
    detail: str = ""
    status: str
    tool: str = ""
    timestamp: str = ""


class CodingRunDetailResponse(BaseModel):
    """Full persisted trace for one coding run."""

    run_id: str
    events: list[dict[str, Any]]
    timeline: list[CodingRunTimelineEntry] = Field(default_factory=list)


class AuthRequest(BaseModel):
    """Passphrase verification request."""

    passphrase: str = Field(min_length=1, description="访问口令")


class AuthResponse(BaseModel):
    """Passphrase verification response."""

    user_id: str
    valid: bool = True


class CloudDevelopmentLoginRequest(BaseModel):
    """Development-only identity bootstrap; production never enables this route."""

    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(default="", max_length=200)
    invite_code: str = Field(min_length=1, max_length=256)

    @field_validator("email", "invite_code")
    @classmethod
    def strip_required_values(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("display_name")
    @classmethod
    def strip_display_name(cls, value: str) -> str:
        return value.strip()


class CloudCurrentUserResponse(BaseModel):
    """Authenticated cloud identity without exposing its browser session token."""

    user_id: str
    email: str
    display_name: str


class CloudGitHubOAuthStartRequest(BaseModel):
    """Start an invite-aware GitHub login without putting secrets in a URL."""

    invite_code: str | None = Field(default=None, max_length=256)
    return_to: str = Field(default="/#/coding", max_length=300)

    @field_validator("invite_code")
    @classmethod
    def strip_optional_invite(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("return_to")
    @classmethod
    def validate_local_return_path(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/#/") or "\n" in value or "\r" in value:
            raise ValueError("return_to must be a local hash route")
        return value


class CloudGitHubOAuthStartResponse(BaseModel):
    """Authorization URL generated by the trusted server."""

    authorization_url: str


class CloudProjectCreateRequest(BaseModel):
    """Metadata required to create a user-owned cloud project."""

    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be blank")
        return value


class CloudProjectResponse(BaseModel):
    """Opaque project metadata returned to its owner."""

    project_id: str
    name: str


class CloudWorkspaceCreateRequest(BaseModel):
    """The provider selector is not a filesystem path or repository URL."""

    provider: Literal["cloud"]


class CloudWorkspaceResponse(BaseModel):
    """Opaque control-plane workspace record without execution details."""

    workspace_id: str
    project_id: str
    provider: str
    lifecycle_state: str


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
