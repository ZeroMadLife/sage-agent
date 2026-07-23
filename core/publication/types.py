"""Stable views for public publication candidates and their audit events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

PublicationCandidateStatus = Literal["pending", "approved", "rejected"]


@dataclass(frozen=True, slots=True)
class PublicationCandidate:
    candidate_id: str
    owner_id: str
    package_id: str
    package_revision: str
    package_digest: str
    package: dict[str, Any]
    reason: str
    evidence_refs: tuple[str, ...]
    status: PublicationCandidateStatus
    revision: int
    decided_by: str | None
    decided_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class PublicationCandidateEvent:
    event_id: str
    candidate_id: str
    sequence: int
    event_type: str
    revision: int
    detail: dict[str, str]
    created_at: str


__all__ = [
    "PublicationCandidate",
    "PublicationCandidateEvent",
    "PublicationCandidateStatus",
]
