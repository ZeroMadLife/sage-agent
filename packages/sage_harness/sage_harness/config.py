"""Pure configuration values for constructing a Sage harness agent."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    """Process-independent safety limits for one agent graph."""

    max_model_calls: int = 24
    max_tool_calls: int = 64
    max_run_tokens: int = 250_000
    recursion_limit: int = 100
    max_run_seconds: float = 1_800.0
    context_compaction_enabled: bool = True
    context_working_set_tokens: int = 32_000
    context_keep_tokens: int = 12_000
    context_summary_input_tokens: int = 32_000
    context_static_overhead_tokens: int = 8_000
    context_min_savings_ratio: float = 0.10
    context_compaction_cooldown_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.max_model_calls < 1:
            raise ValueError("max_model_calls must be positive")
        if self.max_tool_calls < 1:
            raise ValueError("max_tool_calls must be positive")
        if self.max_run_tokens < 1:
            raise ValueError("max_run_tokens must be positive")
        if not 1 <= self.recursion_limit <= 1_000:
            raise ValueError("recursion_limit must be between 1 and 1000")
        if not math.isfinite(self.max_run_seconds) or self.max_run_seconds <= 0:
            raise ValueError("max_run_seconds must be finite and positive")
        if self.context_working_set_tokens < 1:
            raise ValueError("context_working_set_tokens must be positive")
        if not 1 <= self.context_keep_tokens < self.context_working_set_tokens:
            raise ValueError(
                "context_keep_tokens must be positive and below context_working_set_tokens"
            )
        if self.context_summary_input_tokens < 1:
            raise ValueError("context_summary_input_tokens must be positive")
        if self.context_static_overhead_tokens < 0:
            raise ValueError("context_static_overhead_tokens must be non-negative")
        if not math.isfinite(self.context_min_savings_ratio) or not (
            0.0 <= self.context_min_savings_ratio < 1.0
        ):
            raise ValueError("context_min_savings_ratio must be within 0..1")
        if not math.isfinite(self.context_compaction_cooldown_seconds) or (
            self.context_compaction_cooldown_seconds <= 0
        ):
            raise ValueError("context_compaction_cooldown_seconds must be finite and positive")


@dataclass(frozen=True, slots=True)
class HarnessRunContext:
    """Server-owned identity and workspace binding for one graph invocation."""

    thread_id: str
    run_id: str
    owner_id: str
    workspace_id: str
    workspace_path: str
    surface: str = "coding"
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for field_name in (
            "thread_id",
            "run_id",
            "owner_id",
            "workspace_id",
            "workspace_path",
            "surface",
        ):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} must not be empty")


__all__ = ["HarnessConfig", "HarnessRunContext"]
