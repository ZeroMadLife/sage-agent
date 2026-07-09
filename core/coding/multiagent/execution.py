"""Worker task data structures and argument normalization."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from core.coding.context import now


@dataclass
class WorkerTask:
    """One background worker task."""

    id: str
    description: str
    subagent_type: str
    write_scope: tuple[str, ...]
    prompt: str
    thread: threading.Thread | None = None
    status: str = "idle"
    result: str = ""
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)


def clean_type(value: str) -> str:
    """Normalize and validate subagent type."""
    subagent_type = str(value or "worker").strip()
    if subagent_type not in {"worker", "Explore"}:
        raise ValueError("subagent_type must be worker or Explore")
    return subagent_type


def clean_scope(value: list[str] | str | None) -> list[str]:
    """Normalize write-scope tool input into a list of relative paths."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item).strip() for item in value if str(item).strip()]
