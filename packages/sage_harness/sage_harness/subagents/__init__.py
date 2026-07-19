"""Reusable child-agent contracts, middleware, and task tool."""

from sage_harness.subagents.contracts import (
    CancelCheck,
    MasteryEvidenceCandidate,
    MasteryEvidenceKind,
    MasteryEvidenceResult,
    SubagentCancelReason,
    SubagentExecutorPort,
    SubagentLimits,
    SubagentProfile,
    SubagentProgressSink,
    SubagentRequest,
    SubagentResult,
    SubagentTerminalStatus,
    SubagentToolConfig,
    derive_child_run_id,
)
from sage_harness.subagents.middleware import SubagentLifecycleMiddleware
from sage_harness.subagents.tool import build_task_tool

__all__ = [
    "CancelCheck",
    "MasteryEvidenceCandidate",
    "MasteryEvidenceKind",
    "MasteryEvidenceResult",
    "SubagentCancelReason",
    "SubagentExecutorPort",
    "SubagentLifecycleMiddleware",
    "SubagentLimits",
    "SubagentProfile",
    "SubagentProgressSink",
    "SubagentRequest",
    "SubagentResult",
    "SubagentTerminalStatus",
    "SubagentToolConfig",
    "build_task_tool",
    "derive_child_run_id",
]
