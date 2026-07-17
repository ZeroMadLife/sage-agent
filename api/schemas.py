"""API request, response, and WebSocket event schemas."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from models.itinerary import Itinerary


def _default_coding_runtime_profiles() -> list[Literal["legacy", "deerflow_v2"]]:
    return ["legacy"]


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
    runtime_profile: Literal["legacy", "deerflow_v2"] | None = None


class CodingSessionResponse(BaseModel):
    """Response returned when a coding session is created."""

    session_id: str
    workspace_root: str
    workspace_id: str
    permission_mode: Literal["default", "accept_edits", "auto", "plan"] = "default"
    runtime_profile: Literal["legacy", "deerflow_v2"] = "legacy"
    sandbox_provider: str = "local_workspace"
    sandbox_image: str = "python:3.11-slim"


class CodingSessionSummary(BaseModel):
    """One local coding-agent session shown in the Sage workbench."""

    session_id: str
    title: str
    workspace_root: str
    created_at: str = ""
    updated_at: str = ""
    runtime_mode: str = "default"
    runtime_profile: Literal["legacy", "deerflow_v2"] = "legacy"
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


AssistantHomeSectionStatus = Literal["ready", "empty", "not_configured", "unavailable", "error"]


class AssistantHomeIdentity(BaseModel):
    """Browser-safe identity shown on the personal assistant home."""

    mode: Literal["local", "cloud"]
    user_id: str | None = None
    display_name: str


class AssistantHomeKnowledge(BaseModel):
    """Knowledge readiness without exposing source contents."""

    status: AssistantHomeSectionStatus
    source_count: int = Field(ge=0)
    wiki_page_count: int = Field(ge=0)
    last_synced_at: str | None = None


class AssistantHomeRecentSession(BaseModel):
    """One bounded, owner-visible session link."""

    session_id: str
    title: str
    workspace_name: str
    updated_at: str = ""
    message_count: int = Field(ge=0)
    target: str


class AssistantHomeSessions(BaseModel):
    """Recent session section with independent failure state."""

    status: AssistantHomeSectionStatus
    items: list[AssistantHomeRecentSession]
    total: int = Field(ge=0)
    error: str | None = None


class AssistantHomeProject(BaseModel):
    """One owner-scoped cloud project safe for the home page."""

    project_id: str
    name: str


class AssistantHomeProjects(BaseModel):
    """Cloud project section with independent availability state."""

    status: AssistantHomeSectionStatus
    items: list[AssistantHomeProject]
    total: int = Field(ge=0)
    error: str | None = None


class AssistantHomeProposals(BaseModel):
    """Proposal counts only; candidate contents remain in review APIs."""

    status: AssistantHomeSectionStatus
    memory_pending: int = Field(ge=0)
    wiki_pending: int = Field(default=0, ge=0)
    note_pending: int = Field(default=0, ge=0)
    error: str | None = None


class AssistantHomeAction(BaseModel):
    """Deterministic next action derived from current persisted state."""

    id: str
    kind: Literal["chat", "knowledge", "review", "project"]
    label: str
    description: str
    target: str


class AssistantHomeSummary(BaseModel):
    """Bounded read model for the V7 personal assistant home."""

    identity: AssistantHomeIdentity
    knowledge: AssistantHomeKnowledge
    sessions: AssistantHomeSessions
    projects: AssistantHomeProjects
    proposals: AssistantHomeProposals
    suggested_actions: list[AssistantHomeAction] = Field(max_length=4)


class KnowledgeSourceRootSummary(BaseModel):
    """Browser-safe configured source root without a server filesystem path."""

    root_id: str
    kind: Literal["obsidian", "markdown", "github", "feishu"]
    label: str


class KnowledgeWorkspaceSummary(BaseModel):
    """Bounded Knowledge Workspace state."""

    status: Literal["ready"]
    workspace_name: str
    source_count: int = Field(ge=0)
    wiki_page_count: int = Field(ge=0)
    pending_proposal_count: int = Field(ge=0)
    last_synced_at: str | None = None
    source_roots: list[KnowledgeSourceRootSummary]


class KnowledgeSourceStatusResponse(KnowledgeSourceRootSummary):
    """Browser-safe connector state without paths, credentials, or cursors."""

    adapter_id: str
    adapter_version: str
    status: Literal[
        "idle",
        "scanning",
        "planned",
        "running",
        "retryable",
        "cancelled",
        "conflict",
        "failed",
    ]
    watermark: int = Field(ge=0)
    last_error_code: str | None = None
    last_error_message: str | None = None
    last_scan_started_at: str | None = None
    last_scan_completed_at: str | None = None


class KnowledgeSourcesResponse(BaseModel):
    sources: list[KnowledgeSourceStatusResponse]


class KnowledgeIndexResponse(BaseModel):
    status: Literal["ready", "degraded"]
    backend: str
    embedding_model: str
    embedding_revision: str
    revision_count: int = Field(ge=0)
    indexed_revision_count: int = Field(ge=0)
    active_chunk_count: int = Field(ge=0)
    total_chunk_count: int = Field(ge=0)
    error_count: int = Field(ge=0)


class KnowledgeSearchRequest(BaseModel):
    """Retrieve a bounded evidence bundle from approved knowledge revisions."""

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=20)
    token_budget: int = Field(default=3000, ge=256, le=20000)
    visibility: Literal["private", "public"] = "private"
    source_ids: list[str] = Field(default_factory=list, max_length=100)
    page_revisions: list[str] = Field(default_factory=list, max_length=100)


class KnowledgeEvidenceResponse(BaseModel):
    """Browser-safe citation and excerpt for one retrieval hit."""

    citation_id: str
    rank: int = Field(ge=1)
    rrf_score: float = Field(ge=0)
    sparse_rank: int | None = Field(default=None, ge=1)
    sparse_score: float | None = None
    dense_rank: int | None = Field(default=None, ge=1)
    dense_score: float | None = None
    chunk_id: str
    page_id: str
    page_revision: str
    page_path: str
    source_id: str
    source_revision: str
    source_kind: str
    source_relative_path: str
    proposal_id: str
    artifact_id: str | None = None
    block_id: str
    ordinal: int = Field(ge=0)
    title: str
    heading_path: list[str]
    page_number: int | None = Field(default=None, ge=1)
    excerpt: str
    token_count: int = Field(ge=0)
    truncated: bool


class KnowledgeRetrievalResponse(BaseModel):
    """Evidence-only retrieval response; it never synthesizes an uncited answer."""

    query: str
    status: Literal["evidence_found", "no_evidence"]
    token_budget: int = Field(ge=256)
    used_tokens: int = Field(ge=0)
    omitted_count: int = Field(ge=0)
    citations: list[KnowledgeEvidenceResponse]


class KnowledgeCitationResponse(BaseModel):
    """One current, bounded citation for browser evidence inspection."""

    citation_id: str
    chunk_id: str
    page_id: str
    page_revision: str
    page_path: str
    source_id: str
    source_revision: str
    source_kind: str
    source_relative_path: str
    block_id: str
    ordinal: int = Field(ge=0)
    title: str
    heading_path: list[str]
    page_number: int | None = Field(default=None, ge=1)
    excerpt: str
    token_count: int = Field(ge=0)
    truncated: bool


class KnowledgeLearningRequest(BaseModel):
    """Create an extractive learning note from current knowledge citations."""

    topic: str = Field(min_length=1, max_length=160)
    citation_ids: list[str] = Field(min_length=1, max_length=8)
    session_id: str = Field(default="", max_length=128)
    run_id: str = Field(default="", max_length=128)
    event_id: str = Field(default="", max_length=128)


class KnowledgeIngestRequest(BaseModel):
    """Ingest one Markdown file from a server-configured source root."""

    source_root_id: str = Field(min_length=1, max_length=64)
    relative_path: str = Field(min_length=1, max_length=1024)


class KnowledgeBatchIngestRequest(BaseModel):
    """Scan one configured source directory and enqueue its Markdown files."""

    source_root_id: str = Field(min_length=1, max_length=64)
    relative_directory: str = Field(default=".", max_length=1024)
    sync_plan_id: str | None = Field(default=None, min_length=1, max_length=36)


class KnowledgeSyncPlanRequest(KnowledgeBatchIngestRequest):
    """Preview a bounded source manifest diff without starting a worker."""

    sync_plan_id: None = None


class KnowledgeSyncChangeResponse(BaseModel):
    relative_path: str
    change_kind: Literal["added", "modified", "deleted"]
    previous_revision: str | None = None
    source_revision: str | None = None
    idempotency_key: str


class KnowledgeSyncPlanResponse(BaseModel):
    plan_id: str
    workspace_id: str
    source_root_id: str
    relative_directory: str
    pipeline_version: str
    base_watermark: int = Field(ge=0)
    target_watermark: int = Field(ge=0)
    manifest_hash: str
    status: str
    added_count: int = Field(ge=0)
    modified_count: int = Field(ge=0)
    deleted_count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    has_more: bool
    changes: list[KnowledgeSyncChangeResponse]
    created_at: str


class KnowledgeMigrationPlanItemResponse(BaseModel):
    proposal_id: str
    source_root_id: str
    source_relative_path: str
    disposition: Literal["auto_apply", "retire", "review", "block"]
    reason_codes: list[str]
    parser_id: str | None = None


class KnowledgeMigrationPlanResponse(BaseModel):
    plan_id: str
    total: int = Field(ge=0)
    auto_apply_count: int = Field(ge=0)
    retire_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    block_count: int = Field(ge=0)
    items: list[KnowledgeMigrationPlanItemResponse]


class KnowledgeMigrationApplyRequest(BaseModel):
    expected_plan_id: str = Field(min_length=1, max_length=128)


class KnowledgeMigrationResultItemResponse(BaseModel):
    proposal_id: str
    status: Literal["auto_applied", "retired", "review", "blocked", "error"]
    replacement_proposal_id: str | None = None
    reason_code: str | None = None


class KnowledgeMigrationResultResponse(BaseModel):
    plan_id: str
    status: Literal["completed", "completed_with_errors"]
    total: int = Field(ge=0)
    auto_applied_count: int = Field(ge=0)
    retired_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    items: list[KnowledgeMigrationResultItemResponse]


class KnowledgeJobItemResponse(BaseModel):
    item_id: str
    job_id: str
    relative_path: str
    source_revision: str
    change_kind: Literal["added", "modified", "deleted"] = "added"
    status: str
    attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    proposal_id: str | None = None
    error: str | None = None
    next_attempt_at: str | None = None
    updated_at: str


class KnowledgeJobResponse(BaseModel):
    job_id: str
    workspace_id: str
    source_root_id: str
    source_kind: str
    source_label: str
    relative_directory: str
    pipeline_version: str
    status: str
    cancel_requested: bool
    total_items: int = Field(ge=0)
    processed_items: int = Field(ge=0)
    succeeded_items: int = Field(ge=0)
    skipped_items: int = Field(ge=0)
    failed_items: int = Field(ge=0)
    cancelled_items: int = Field(ge=0)
    latest_sequence: int = Field(ge=0)
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str
    sync_plan_id: str | None = None
    items: list[KnowledgeJobItemResponse] = Field(default_factory=list)


class KnowledgeJobsResponse(BaseModel):
    jobs: list[KnowledgeJobResponse]


class KnowledgeJobEventResponse(BaseModel):
    event_id: str
    job_id: str
    item_id: str | None = None
    sequence: int = Field(ge=1)
    kind: str
    status: str
    detail: dict[str, str | int | bool | None]
    created_at: str


class KnowledgeJobEventsResponse(BaseModel):
    items: list[KnowledgeJobEventResponse]
    next_cursor: int = Field(ge=0)
    has_more: bool


class KnowledgeTransitionRequest(BaseModel):
    """Optimistic revision guard for a proposal decision."""

    expected_revision: int = Field(ge=0)


class KnowledgeUndoAutoApplyRequest(BaseModel):
    expected_page_revision: str = Field(min_length=1, max_length=128)


class KnowledgePolicyDecisionResponse(BaseModel):
    decision_id: str
    policy_id: str
    policy_version: str
    risk_level: Literal["low", "medium", "high", "blocked"]
    action: Literal["auto_apply", "draft", "require_review", "block"]
    reason_codes: list[str]
    applied_page_revision: str | None = None
    undo_available: bool
    undo_proposal_id: str | None = None
    undo_page_revision: str | None = None
    undone_at: str | None = None


class KnowledgeProposalResponse(BaseModel):
    """One reviewable Wiki change without server absolute paths."""

    proposal_id: str
    source_root_id: str
    source_kind: str
    source_relative_path: str
    source_revision: str
    raw_path: str
    page_id: str
    target_path: str
    title: str
    base_page_revision: str
    change_kind: Literal["ingest", "rollback", "synthesis", "retraction", "learning"]
    status: Literal["pending", "approved", "rejected"]
    projection_status: Literal["pending", "complete", "error"]
    revision: int = Field(ge=0)
    parse_artifact_id: str | None = None
    error: str | None = None
    policy_decision: KnowledgePolicyDecisionResponse | None = None
    diff: str
    diff_truncated: bool
    created_at: str
    updated_at: str


class KnowledgeProposalsResponse(BaseModel):
    proposals: list[KnowledgeProposalResponse]


class KnowledgeProposalEvent(BaseModel):
    event_id: str
    event_type: str
    revision: int = Field(ge=0)
    detail: dict[str, str]
    created_at: str


class KnowledgeParseBlockResponse(BaseModel):
    """Non-sensitive block evidence; source text remains server-side."""

    block_id: str
    ordinal: int = Field(ge=0)
    kind: Literal["frontmatter", "heading", "paragraph", "list", "code", "table", "quote", "media"]
    heading_path: list[str]
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    media_ref: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class KnowledgeParseArtifactResponse(BaseModel):
    artifact_id: str
    document_id: str
    parser_id: str
    parser_version: str
    source_revision: str
    media_type: str
    title: str
    language: str
    block_count: int = Field(ge=0)
    blocks: list[KnowledgeParseBlockResponse]
    created_at: str


class KnowledgeUnderstandingCitationResponse(BaseModel):
    block_id: str
    page: int | None = None
    heading_path: list[str]


class KnowledgeUnderstandingSectionResponse(BaseModel):
    title: str
    block_ids: list[str]


class KnowledgeSourceUnderstandingResponse(BaseModel):
    understanding_id: str
    artifact_id: str
    source_revision: str
    title: str
    summary: str
    sections: list[KnowledgeUnderstandingSectionResponse]
    topics: list[str]
    block_kind_counts: dict[str, int]
    citations: list[KnowledgeUnderstandingCitationResponse]
    generator_id: str
    generator_version: str


class KnowledgeSynthesisSourceResponse(BaseModel):
    page_id: str
    page_revision: str
    proposal_id: str
    understanding_id: str
    source_revision: str
    title: str
    path: str
    summary: str
    topics: list[str]
    citation_block_ids: list[str]


class KnowledgeWorkspaceSynthesisResponse(BaseModel):
    synthesis_id: str
    input_hash: str
    generator_id: str
    generator_version: str
    sources: list[KnowledgeSynthesisSourceResponse]


class KnowledgeProposalDetailResponse(BaseModel):
    proposal: KnowledgeProposalResponse
    events: list[KnowledgeProposalEvent]
    parse_artifact: KnowledgeParseArtifactResponse | None = None
    source_understanding: KnowledgeSourceUnderstandingResponse | None = None
    workspace_synthesis: KnowledgeWorkspaceSynthesisResponse | None = None


class KnowledgePageRevisionResponse(BaseModel):
    revision_id: str
    sequence: int = Field(ge=1)
    content_hash: str
    source_revision: str
    proposal_id: str
    change_kind: Literal["ingest", "rollback", "synthesis", "retraction", "learning"]
    git_commit: str
    created_at: str


class KnowledgePageResponse(BaseModel):
    page_id: str
    path: str
    title: str
    current_revision: str
    updated_at: str
    revisions: list[KnowledgePageRevisionResponse]


class KnowledgePageDocumentResponse(BaseModel):
    page_id: str
    path: str
    title: str
    updated_at: str
    revision: KnowledgePageRevisionResponse
    content: str
    truncated: bool


class KnowledgePagesResponse(BaseModel):
    pages: list[KnowledgePageResponse]


class KnowledgeGraphSnapshotResponse(BaseModel):
    graph_revision: str
    workspace_id: str
    wiki_watermark: str
    projector_id: str
    projector_version: str
    config_hash: str
    status: Literal["building", "ready", "error"]
    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)
    error: str | None = None
    created_at: str
    completed_at: str | None = None
    stale: bool = False


class KnowledgeGraphEvidenceResponse(BaseModel):
    citation_id: str
    chunk_id: str
    page_id: str
    page_revision: str
    source_id: str
    source_revision: str


class KnowledgeGraphNodeResponse(BaseModel):
    node_id: str
    kind: Literal["page", "source", "project", "concept", "decision", "tool"]
    label: str
    page_id: str | None = None
    page_revision: str | None = None
    source_id: str | None = None
    source_revision: str | None = None
    properties: dict[str, Any]


class KnowledgeGraphEdgeResponse(BaseModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    kind: Literal["WIKILINK", "EVIDENCED_BY", "SHARES_SOURCE"]
    directed: bool
    weight: float = Field(gt=0)
    confidence: float = Field(ge=0, le=1)
    extractor_id: str
    extractor_version: str
    properties: dict[str, Any]
    evidence: list[KnowledgeGraphEvidenceResponse]


class KnowledgeGraphResponse(BaseModel):
    snapshot: KnowledgeGraphSnapshotResponse
    nodes: list[KnowledgeGraphNodeResponse]
    edges: list[KnowledgeGraphEdgeResponse]
    offset: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)
    has_more: bool


class KnowledgeGraphNodeDetailResponse(BaseModel):
    snapshot: KnowledgeGraphSnapshotResponse
    node: KnowledgeGraphNodeResponse


class KnowledgeGraphNeighborhoodResponse(BaseModel):
    snapshot: KnowledgeGraphSnapshotResponse
    center: KnowledgeGraphNodeResponse
    nodes: list[KnowledgeGraphNodeResponse]
    edges: list[KnowledgeGraphEdgeResponse]


class KnowledgeGraphStatusResponse(BaseModel):
    status: Literal["unbuilt", "building", "ready", "error"]
    snapshot: KnowledgeGraphSnapshotResponse | None = None


class KnowledgeLearningCapabilityInput(BaseModel):
    capability_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    label: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    keywords: list[str] = Field(min_length=1, max_length=24)
    weight: float = Field(default=1.0, ge=0.1, le=10.0)
    required: bool = True


class KnowledgeLearningGoalUpdateRequest(BaseModel):
    expected_goal_revision: str = Field(min_length=1, max_length=96)
    goal_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)
    capabilities: list[KnowledgeLearningCapabilityInput] = Field(max_length=32)


class KnowledgeLearningCapabilityResponse(KnowledgeLearningCapabilityInput):
    pass


class KnowledgeLearningGoalResponse(BaseModel):
    schema_version: int = Field(ge=1)
    goal_id: str
    title: str
    description: str
    capabilities: list[KnowledgeLearningCapabilityResponse]
    goal_revision: str
    git_commit: str
    structured: bool


class KnowledgeGraphAnalysisSnapshotResponse(BaseModel):
    analysis_revision: str
    workspace_id: str
    graph_revision: str
    goal_revision: str
    algorithm_id: str
    algorithm_version: str
    seed: int
    resolution: float
    threshold: float
    status: Literal["building", "ready", "error"]
    community_count: int = Field(ge=0)
    insight_count: int = Field(ge=0)
    error: str | None = None
    created_at: str
    completed_at: str | None = None


class KnowledgeGraphCommunityResponse(BaseModel):
    community_id: str
    label: str
    node_count: int = Field(ge=1)
    edge_count: int = Field(ge=0)
    cohesion: float = Field(ge=0, le=1)
    properties: dict[str, Any]


class KnowledgeGraphNodeMetricResponse(BaseModel):
    node_id: str
    community_id: str
    degree: int = Field(ge=0)
    weighted_degree: float = Field(ge=0)
    bridge_score: float = Field(ge=0, le=1)


class KnowledgeGoalAlignmentResponse(BaseModel):
    capability_id: str
    label: str
    coverage: float = Field(ge=0, le=1)
    status: Literal["covered", "learning", "gap"]
    matched_keywords: list[str]
    missing_keywords: list[str]
    matched_node_ids: list[str]


class KnowledgeGraphInsightResponse(BaseModel):
    insight_id: str
    kind: Literal[
        "missing_concept",
        "isolated_node",
        "bridge_node",
        "sparse_community",
        "capability_gap",
    ]
    severity: Literal["low", "medium", "high"]
    title: str
    description: str
    node_id: str | None = None
    community_id: str | None = None
    capability_id: str | None = None
    properties: dict[str, Any]


class KnowledgeGraphCommunitiesResponse(BaseModel):
    analysis: KnowledgeGraphAnalysisSnapshotResponse
    communities: list[KnowledgeGraphCommunityResponse]
    node_metrics: list[KnowledgeGraphNodeMetricResponse]


class KnowledgeGraphInsightsResponse(BaseModel):
    analysis: KnowledgeGraphAnalysisSnapshotResponse
    goal: KnowledgeLearningGoalResponse
    alignments: list[KnowledgeGoalAlignmentResponse]
    insights: list[KnowledgeGraphInsightResponse]


class KnowledgeRollbackRequest(BaseModel):
    target_revision_id: str = Field(min_length=1, max_length=128)
    expected_page_revision: str = Field(min_length=1, max_length=128)


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
    runtime_profiles: list[Literal["legacy", "deerflow_v2"]] = Field(
        default_factory=_default_coding_runtime_profiles
    )
    default_runtime_profile: Literal["legacy", "deerflow_v2"] = "legacy"


class CodingModelSwitchRequest(BaseModel):
    """Request body for switching a session's model."""

    model_id: str = Field(min_length=1)


class CodingReasoningSwitchRequest(BaseModel):
    """Select a server-declared reasoning mode for a coding session."""

    mode: Literal["off", "low", "medium", "high"] = "off"


class CodingProviderReasoningInput(BaseModel):
    """Non-secret model reasoning descriptor submitted by the settings editor."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["unsupported", "openai_reasoning_effort", "anthropic_thinking_budget"]
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


class CodingRunAuditStep(BaseModel):
    """One bounded, deterministic tool step projected from persisted evidence."""

    tool: str
    status: str
    action_summary: str
    result_summary: str
    duration_ms: int = Field(default=0, ge=0)
    arguments_preview: str = ""
    result_preview: str = ""
    arguments_truncated: bool = False
    result_truncated: bool = False


class CodingRunAuditSummary(BaseModel):
    """Safe run-level audit projection for history and chat surfaces."""

    run_id: str
    status: str
    headline: str
    tool_count: int = Field(ge=0)
    completed_tool_count: int = Field(ge=0)
    failed_tool_count: int = Field(ge=0)
    approval_count: int = Field(ge=0)
    duration_ms: int = Field(default=0, ge=0)
    changed_files: list[str] = Field(default_factory=list)
    steps: list[CodingRunAuditStep] = Field(default_factory=list)


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
    audit: CodingRunAuditSummary


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
    audit: CodingRunAuditSummary


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


class CloudModelInput(BaseModel):
    """One manually configured or discovered model under a Provider."""

    model_id: str = Field(min_length=1, max_length=255)
    display_name: str = Field(default="", max_length=255)
    context_window_tokens: int | None = Field(default=None, ge=1024, le=2_000_000)
    output_reserve_tokens: int | None = Field(default=None, ge=1, le=500_000)
    reasoning_supported: bool = False

    @field_validator("model_id", "display_name")
    @classmethod
    def strip_model_values(cls, value: str) -> str:
        return value.strip()


class CloudModelProviderCreateRequest(BaseModel):
    """Create an account-scoped Provider with a write-only API key."""

    name: str = Field(min_length=1, max_length=120)
    api_mode: Literal["openai_chat_completions", "openai_responses", "anthropic_messages"]
    base_url: str = Field(min_length=1, max_length=500)
    api_key: SecretStr
    models: list[CloudModelInput] = Field(min_length=1, max_length=256)
    default_model_id: str | None = Field(default=None, max_length=255)

    @field_validator("name", "base_url", "default_model_id")
    @classmethod
    def strip_provider_create_values(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class CloudModelProviderUpdateRequest(BaseModel):
    """Update Provider metadata; absent API key preserves the encrypted value."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    api_mode: (
        Literal["openai_chat_completions", "openai_responses", "anthropic_messages"] | None
    ) = None
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    api_key: SecretStr | None = None
    models: list[CloudModelInput] | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("name", "base_url")
    @classmethod
    def strip_provider_update_values(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class CloudModelResponse(BaseModel):
    id: str
    runtime_id: str
    model_id: str
    display_name: str
    context_window_tokens: int | None = None
    output_reserve_tokens: int | None = None
    reasoning_supported: bool = False


class CloudModelProviderResponse(BaseModel):
    id: str
    name: str
    api_mode: str
    base_url: str
    key_configured: bool = True
    key_hint: str
    status: str
    last_tested_at: str | None = None
    models: list[CloudModelResponse]


class CloudModelProvidersResponse(BaseModel):
    providers: list[CloudModelProviderResponse]
    default_model: str | None = None


class CloudModelDefaultRequest(BaseModel):
    provider_id: str = Field(min_length=1, max_length=36)
    model_id: str = Field(min_length=1, max_length=255)


class CloudModelDefaultResponse(BaseModel):
    provider_id: str
    model_id: str
    runtime_model_id: str


class CloudModelProviderTestResponse(BaseModel):
    ok: bool
    status: str
    tested_at: str


class CloudModelDiscoveryResponse(BaseModel):
    models: list[str]


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


class HarnessOperationRef(BaseModel):
    """A bounded reference to a canonical operation owned by another store."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["knowledge_job", "coding_run"]
    id: str = Field(min_length=1, max_length=128)


class HarnessResourceContext(BaseModel):
    """The stable resource bound to one submitted turn."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["knowledge_page", "knowledge_source", "coding_workspace"]
    id: str = Field(min_length=1, max_length=512)
    revision: str | None = Field(default=None, min_length=1, max_length=256)
    label: str | None = Field(default=None, min_length=1, max_length=256)


class HarnessSelectionContext(BaseModel):
    """The stable selection inside a bound Harness resource."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal["graph_node", "knowledge_page", "knowledge_source", "coding_file"]
    id: str = Field(min_length=1, max_length=1024)
    revision: str | None = Field(default=None, min_length=1, max_length=256)
    label: str | None = Field(default=None, min_length=1, max_length=256)


class HarnessSurfaceContext(BaseModel):
    """Client-proposed context that must be canonicalized before a run starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    surface: Literal["growth", "knowledge", "coding"]
    workspace_id: str = Field(min_length=1, max_length=128)
    resource: HarnessResourceContext | None = None
    selection: HarnessSelectionContext | None = None
    graph_revision: str | None = Field(default=None, min_length=1, max_length=256)
    operation_refs: list[HarnessOperationRef] = Field(default_factory=list, max_length=20)


class UserMessage(BaseModel):
    """用户通过 WebSocket 发送的消息。"""

    content: str = Field(min_length=1, description="用户消息内容")
    surface_context: HarnessSurfaceContext | None = None


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
