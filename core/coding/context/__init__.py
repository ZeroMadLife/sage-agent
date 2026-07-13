"""Context, prompt-budget, and workspace public API."""

from core.coding.context.budget import ContextPolicy, ContextUsage, TokenCounter
from core.coding.context.compact import (
    CheckpointVerifier,
    CompactionBusyError,
    CompactionPolicy,
    CompactManager,
    Summarizer,
)
from core.coding.context.controller import (
    ContextBusyError,
    ContextController,
    ContextLifecycleSinkError,
    PreparedContext,
)
from core.coding.context.manager import (
    DEFAULT_SYSTEM_PROMPT,
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    ContextManager,
    SectionRender,
    normalize_text,
    tail_clip,
)
from core.coding.context.model_capabilities import ModelCapabilityRegistry
from core.coding.context.model_manifest import CodingModelDefinition, CodingModelManifest
from core.coding.context.projection import ContextLevel, ContextProjector
from core.coding.context.summarizer import StructuredSummarizer
from core.coding.context.summary import (
    CompactionCheckpoint,
    CompactionResult,
    CompactionSummary,
)
from core.coding.context.workspace import (
    IGNORED_PATH_NAMES,
    WorkspaceContext,
    clip,
    now,
)
from core.coding.context.workspace_diff import (
    MAX_DIFF_FILES,
    MAX_FILE_SIZE,
    FileChange,
    FileSnapshot,
    WorkspaceDiff,
    WorkspaceDiffTracker,
)

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "IGNORED_PATH_NAMES",
    "MAX_DIFF_FILES",
    "MAX_FILE_SIZE",
    "SYSTEM_PROMPT_DYNAMIC_BOUNDARY",
    "CheckpointVerifier",
    "CodingModelDefinition",
    "CodingModelManifest",
    "CompactManager",
    "CompactionBusyError",
    "CompactionCheckpoint",
    "CompactionPolicy",
    "CompactionResult",
    "CompactionSummary",
    "ContextBusyError",
    "ContextController",
    "ContextLevel",
    "ContextLifecycleSinkError",
    "ContextManager",
    "ContextPolicy",
    "ContextProjector",
    "ContextUsage",
    "FileChange",
    "FileSnapshot",
    "ModelCapabilityRegistry",
    "PreparedContext",
    "SectionRender",
    "StructuredSummarizer",
    "Summarizer",
    "TokenCounter",
    "WorkspaceContext",
    "WorkspaceDiff",
    "WorkspaceDiffTracker",
    "clip",
    "normalize_text",
    "now",
    "tail_clip",
]
