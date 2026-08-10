"""Sage application adapters and migration contracts for sage_harness."""

from core.harness.profile import RuntimeProfile, normalize_runtime_profile
from core.harness.task_intent import TaskIntentAnalyzer, TaskIntentEnvelope

__all__ = [
    "RuntimeProfile",
    "TaskIntentAnalyzer",
    "TaskIntentEnvelope",
    "normalize_runtime_profile",
]
