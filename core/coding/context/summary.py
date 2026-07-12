"""Typed artifacts produced by structured context compaction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
)

_HANDOFF_WARNING = "Historical handoff only; the latest user message always wins."
_Text = Annotated[StrictStr, StringConstraints(max_length=8_000)]
_Reference = Annotated[StrictStr, StringConstraints(min_length=1, max_length=1_024)]
_TextItems = Annotated[tuple[_Text, ...], Field(max_length=128)]
_References = Annotated[tuple[_Reference, ...], Field(max_length=256)]


class CompactionSummary(BaseModel):
    """Validated semantic handoff for history removed from the active projection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: _Text
    user_constraints: _TextItems = ()
    decisions: _TextItems = ()
    completed_work: _TextItems = ()
    active_todos: _References = ()
    files_read: _References = ()
    files_modified: _References = ()
    tests: _References = ()
    errors: _TextItems = ()
    artifact_refs: _References = ()
    next_steps: _TextItems = ()
    source_transcript_range: tuple[StrictInt, StrictInt]
    source_run_ids: _References = ()

    @field_validator("source_transcript_range", mode="before")
    @classmethod
    def _normalize_range(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @field_validator("source_transcript_range")
    @classmethod
    def _validate_range(cls, value: tuple[int, int]) -> tuple[int, int]:
        if value[0] < 0 or value[1] < value[0]:
            raise ValueError("source transcript range must be non-negative and ordered")
        return value

    def render_for_prompt(self) -> str:
        """Render a deterministic prompt block with an instruction-precedence warning."""
        body = json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"{_HANDOFF_WARNING}\n{body}"


@dataclass(frozen=True)
class CompactionCheckpoint:
    compaction_id: str
    transcript_start: int
    transcript_end: int
    summary: CompactionSummary
    summary_hash: str
    previous_summary_hash: str = ""
    evidence_hash: str = ""
    prefix_hash: str = ""


@dataclass(frozen=True)
class CompactionResult:
    applied: bool
    projected_history: list[dict[str, Any]]
    checkpoint: CompactionCheckpoint | None
    before_tokens: int
    after_tokens: int
    archived_items: int
    reason: str = ""
    compaction_id: str = ""
    trigger: str = "manual"
    retryable: bool = False
    cooldown_until: float | None = None
