"""Context, prompt-budget, and workspace public API."""

from core.coding.context.compact import CompactManager
from core.coding.context.manager import (
    DEFAULT_SYSTEM_PROMPT,
    SYSTEM_PROMPT_DYNAMIC_BOUNDARY,
    ContextManager,
    SectionRender,
    normalize_text,
    tail_clip,
)
from core.coding.context.workspace import (
    IGNORED_PATH_NAMES,
    WorkspaceContext,
    clip,
    now,
)

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "IGNORED_PATH_NAMES",
    "SYSTEM_PROMPT_DYNAMIC_BOUNDARY",
    "CompactManager",
    "ContextManager",
    "SectionRender",
    "WorkspaceContext",
    "clip",
    "normalize_text",
    "now",
    "tail_clip",
]
