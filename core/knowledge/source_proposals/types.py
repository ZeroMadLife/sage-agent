"""Public control-plane views for reviewable Knowledge source proposals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

KnowledgeSourceProposalStatus = Literal["pending", "applying", "approved", "rejected"]


@dataclass(frozen=True, slots=True)
class KnowledgeSourceProposal:
    proposal_id: str
    workspace_id: str
    owner_id: str
    thread_id: str
    run_id: str
    artifact_ref: str
    source_kind: str
    canonical_url: str
    title: str
    media_type: str
    retrieved_at: str
    content_hash: str
    reason: str
    evidence_refs: tuple[str, ...]
    status: KnowledgeSourceProposalStatus
    revision: int
    target_root_id: str
    target_relative_path: str
    job_id: str | None
    last_error: str | None
    decided_by: str | None
    decided_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class KnowledgeSourceProposalEvent:
    event_id: str
    proposal_id: str
    sequence: int
    event_type: str
    revision: int
    detail: dict[str, str]
    created_at: str


__all__ = [
    "KnowledgeSourceProposal",
    "KnowledgeSourceProposalEvent",
    "KnowledgeSourceProposalStatus",
]
