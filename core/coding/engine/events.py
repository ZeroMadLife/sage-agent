"""Typed runtime events for the Sage coding agent."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field


def _event_now() -> str:
    return datetime.now(UTC).isoformat()


class RunEventBase(BaseModel):
    """Base fields shared by every streamable run event."""

    type: str
    run_id: str = ""
    created_at: str = Field(default_factory=_event_now)


class TurnStartedEvent(RunEventBase):
    """A runtime turn has started."""

    type: Literal["turn_started"] = "turn_started"


class ModelRequestedEvent(RunEventBase):
    """The engine is about to call the model."""

    type: Literal["model_requested"] = "model_requested"
    attempts: int = 0
    tool_steps: int = 0
    prompt_chars: int = 0


class ModelParsedEvent(RunEventBase):
    """The model response was parsed into protocol kind and payload."""

    type: Literal["model_parsed"] = "model_parsed"
    kind: str = ""


class ToolCallEvent(RunEventBase):
    """A tool call is starting."""

    type: Literal["tool_call"] = "tool_call"
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class ApprovalRequiredEvent(RunEventBase):
    """A tool call is blocked on user approval."""

    type: Literal["approval_required"] = "approval_required"
    approval_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    description: str
    pattern_key: str


class ApprovalGrantedEvent(RunEventBase):
    """A pending approval was granted or already satisfied."""

    type: Literal["approval_granted"] = "approval_granted"
    tool: str


class ToolResultEvent(RunEventBase):
    """A tool call completed or failed before execution."""

    type: Literal["tool_result"] = "tool_result"
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    content: str
    is_error: bool = False
    policy_reason: str | None = None
    security_event_type: str | None = None


class RetryEvent(RunEventBase):
    """The model did not follow protocol and should retry."""

    type: Literal["retry"] = "retry"
    content: str


class FinalEvent(RunEventBase):
    """The assistant has produced a final answer."""

    type: Literal["final"] = "final"
    content: str


class StepLimitEvent(RunEventBase):
    """The agent loop reached its configured step limit."""

    type: Literal["step_limit"] = "step_limit"
    content: str


class TextDeltaEvent(RunEventBase):
    """A chunk of streamed model output text."""

    type: Literal["text_delta"] = "text_delta"
    delta: str = ""


class CancelledEvent(RunEventBase):
    """The current run was cancelled."""

    type: Literal["cancelled"] = "cancelled"
    content: str


class ErrorEvent(RunEventBase):
    """The runtime encountered an error."""

    type: Literal["error"] = "error"
    message: str


class TurnFinishedEvent(RunEventBase):
    """A runtime turn has finished streaming."""

    type: Literal["turn_finished"] = "turn_finished"


class RuntimeModeChangedEvent(RunEventBase):
    """Runtime mode changed (plan/default) during tool execution."""

    type: Literal["runtime_mode_changed"] = "runtime_mode_changed"
    mode: str = "default"
    topic: str = ""
    plan_path: str = ""


class PlanReadyForReviewEvent(RunEventBase):
    """Plan is ready for user review before exiting plan mode."""

    type: Literal["plan_ready_for_review"] = "plan_ready_for_review"
    review_id: str = ""
    plan_path: str = ""
    summary: str = ""


class RunFinishedEvent(RunEventBase):
    """A run has reached a terminal state and its lease is released."""

    type: Literal["run_finished"] = "run_finished"
    status: str = "completed"
    duration_ms: int = 0
    tool_steps: int = 0


class WorkspaceDiffReadyEvent(RunEventBase):
    """Workspace diff artifact is ready after a run."""

    type: Literal["workspace_diff_ready"] = "workspace_diff_ready"
    changed_files: list[str] = Field(default_factory=list)
    file_count: int = 0
    truncated: bool = False


class MemoryProposalReadyEvent(RunEventBase):
    """Memory consolidation proposals are ready for user review."""

    type: Literal["memory_proposal_ready"] = "memory_proposal_ready"
    session_id: str
    run_id: str
    reflection_id: str
    proposal_id: str
    candidate_count: int = Field(ge=0)
    base_revision: int = Field(ge=0)


class ContextUsageUpdatedEvent(RunEventBase):
    """The effective model context pressure changed."""

    type: Literal["context_usage_updated"] = "context_usage_updated"
    session_id: str
    run_id: str
    used_tokens: int = Field(ge=0)
    model_limit_tokens: int = Field(gt=0)
    output_reserve_tokens: int = Field(gt=0)
    effective_limit_tokens: int = Field(gt=0)
    usage_ratio: float = Field(ge=0)
    level: Literal["normal", "budget", "snip", "compact", "high", "emergency"]
    estimated: bool
    compactable: bool


class ContextCompactionStartedEvent(RunEventBase):
    """A semantic context compaction attempt started."""

    type: Literal["context_compaction_started"] = "context_compaction_started"
    session_id: str
    compaction_id: str
    trigger: str
    before_tokens: int = Field(ge=0)


class ContextCompactionCompletedEvent(RunEventBase):
    """A semantic context compaction attempt completed."""

    type: Literal["context_compaction_completed"] = "context_compaction_completed"
    session_id: str
    compaction_id: str
    before_tokens: int = Field(ge=0)
    after_tokens: int = Field(ge=0)
    archived_items: int = Field(ge=0)
    saved_ratio: float = Field(default=0.0, ge=0, le=1)

    def model_post_init(self, context: Any, /) -> None:
        del context
        ratio = 0.0
        if self.before_tokens > 0:
            ratio = min(1.0, max(0.0, (self.before_tokens - self.after_tokens) / self.before_tokens))
        object.__setattr__(self, "saved_ratio", ratio)


class ContextCompactionFailedEvent(RunEventBase):
    """A semantic context compaction attempt failed without losing evidence."""

    type: Literal["context_compaction_failed"] = "context_compaction_failed"
    session_id: str
    compaction_id: str
    reason: str
    preserved_original: bool = True
    retryable: bool = False


RunEvent: TypeAlias = (
    TurnStartedEvent
    | ModelRequestedEvent
    | ModelParsedEvent
    | ToolCallEvent
    | ApprovalRequiredEvent
    | ApprovalGrantedEvent
    | ToolResultEvent
    | RetryEvent
    | FinalEvent
    | StepLimitEvent
    | TextDeltaEvent
    | CancelledEvent
    | ErrorEvent
    | TurnFinishedEvent
    | RuntimeModeChangedEvent
    | PlanReadyForReviewEvent
    | WorkspaceDiffReadyEvent
    | MemoryProposalReadyEvent
    | ContextUsageUpdatedEvent
    | ContextCompactionStartedEvent
    | ContextCompactionCompletedEvent
    | ContextCompactionFailedEvent
    | RunFinishedEvent
)


def event_to_dict(event: RunEventBase) -> dict[str, Any]:
    """Serialize a typed event to the existing JSON-safe dict wire shape."""
    return event.model_dump()
