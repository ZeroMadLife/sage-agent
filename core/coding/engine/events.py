"""Typed runtime events for the Sage coding agent."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

from pydantic import BaseModel, Field

from core.coding.context import now


class RunEventBase(BaseModel):
    """Base fields shared by every streamable run event."""

    type: str
    run_id: str = ""
    created_at: str = Field(default_factory=now)


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
    | RunFinishedEvent
)


def event_to_dict(event: RunEventBase) -> dict[str, Any]:
    """Serialize a typed event to the existing JSON-safe dict wire shape."""
    return event.model_dump()
