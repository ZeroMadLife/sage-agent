"""Engine-layer public API for Sage coding runtime."""

from core.coding.engine.events import (
    ApprovalGrantedEvent,
    ApprovalRequiredEvent,
    CancelledEvent,
    ErrorEvent,
    FinalEvent,
    ModelParsedEvent,
    ModelRequestedEvent,
    PlanReadyForReviewEvent,
    RetryEvent,
    RunEvent,
    RunEventBase,
    RunFinishedEvent,
    RuntimeModeChangedEvent,
    StepLimitEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    TurnFinishedEvent,
    TurnStartedEvent,
    event_to_dict,
)
from core.coding.engine.helpers import (
    build_tool_descriptions,
    normalize_tool_payload,
    step_limit_summary,
)
from core.coding.engine.model_output import parse

__all__ = [
    "ApiClient",
    "ApprovalGrantedEvent",
    "ApprovalRequiredEvent",
    "CancelledEvent",
    "Engine",
    "ErrorEvent",
    "FinalEvent",
    "ModelClient",
    "ModelParsedEvent",
    "ModelRequestedEvent",
    "PlanReadyForReviewEvent",
    "RetryEvent",
    "RunEvent",
    "RunEventBase",
    "RunFinishedEvent",
    "RuntimeModeChangedEvent",
    "StepLimitEvent",
    "TextDeltaEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "TurnFinishedEvent",
    "TurnStartedEvent",
    "build_tool_descriptions",
    "event_to_dict",
    "normalize_tool_payload",
    "parse",
    "step_limit_summary",
]


def __getattr__(name: str) -> object:
    """Lazily expose the Engine class to avoid import cycles with tool execution."""
    if name in {"ApiClient", "Engine", "ModelClient"}:
        from core.coding.engine.engine import ApiClient, Engine, ModelClient

        exports = {
            "ApiClient": ApiClient,
            "Engine": Engine,
            "ModelClient": ModelClient,
        }
        return exports[name]
    raise AttributeError(name)
