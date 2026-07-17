"""Proposal-only adapter from the reusable Harness memory port to Sage storage."""

from __future__ import annotations

import hashlib
from typing import Literal, cast

from sage_harness import MemoryPort, MemoryProposalReceipt, MemoryReference

from core.coding.memory import MemoryCandidate
from core.coding.runtime import CodingRuntime

_MAX_PROPOSAL_CHARS = 4_000
_MAX_CONTEXT_REFS = 32
_TOPICS = frozenset({"project-conventions", "decisions"})


class CodingMemoryPort(MemoryPort):
    """Keep model-selected facts pending until Sage's existing CAS approval."""

    def __init__(self, runtime: CodingRuntime) -> None:
        self.runtime = runtime

    async def load_context(
        self,
        thread_id: str,
        *,
        token_budget: int,
    ) -> tuple[MemoryReference, ...]:
        self._require_thread(thread_id)
        if token_budget < 1:
            raise ValueError("token_budget must be positive")
        remaining_chars = min(8_000, token_budget * 4)
        references: list[MemoryReference] = []
        for fact in reversed(self.runtime.memory_manager.list_facts()):
            summary = " ".join(str(fact.content).split())
            if not summary or remaining_chars < 1:
                continue
            summary = summary[: min(500, remaining_chars)]
            identity = "\0".join(
                (str(fact.topic), str(fact.source_ref), str(fact.created_at), summary)
            )
            references.append(
                MemoryReference(
                    memory_id="memory_"
                    + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
                    summary=summary,
                    revision=str(fact.created_at)[:80],
                    metadata={"topic": str(fact.topic)[:120]},
                )
            )
            remaining_chars -= len(summary)
            if len(references) >= _MAX_CONTEXT_REFS:
                break
        return tuple(references)

    async def propose(
        self,
        thread_id: str,
        run_id: str,
        content: str,
        *,
        topic: str = "project-conventions",
    ) -> MemoryProposalReceipt:
        self._require_thread(thread_id)
        if self.runtime.active_run_id != run_id:
            raise PermissionError("memory proposal run is not active")
        normalized = " ".join(content.split())
        if not normalized:
            raise ValueError("memory proposal content must not be empty")
        if len(normalized) > _MAX_PROPOSAL_CHARS:
            raise ValueError("memory proposal content is too long")
        if topic not in _TOPICS:
            raise ValueError("unsupported memory proposal topic")

        identity = "\0".join((thread_id, run_id, topic, normalized))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        reflection_id = f"harness_{run_id}"
        proposal = self.runtime.memory_manager.create_proposal(
            [
                MemoryCandidate(
                    content=normalized,
                    topic=topic,
                    source="harness_proposal",
                    source_ref=run_id,
                )
            ],
            session_id=thread_id,
            run_id=run_id,
            reflection_id=reflection_id,
            proposal_id=f"prop_{digest}",
        )
        if proposal.status not in {"pending", "approved", "rejected"}:
            raise RuntimeError("memory proposal returned an invalid status")
        status = cast(Literal["pending", "approved", "rejected"], proposal.status)
        return MemoryProposalReceipt(
            proposal_id=proposal.proposal_id,
            thread_id=thread_id,
            run_id=run_id,
            reflection_id=proposal.reflection_id,
            status=status,
            candidate_count=len(proposal.candidates),
            base_revision=proposal.base_revision,
        )

    def _require_thread(self, thread_id: str) -> None:
        if thread_id != self.runtime.session_id:
            raise PermissionError("memory proposal thread does not match runtime")


__all__ = ["CodingMemoryPort"]
