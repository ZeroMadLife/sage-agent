"""Deterministic, proposal-only consolidation of sourced run evidence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from core.coding.persistence.memory_store import MemoryCandidate, MemoryStoredFact


@dataclass(frozen=True, slots=True)
class EpisodicEvidence:
    run_id: str
    content: str
    topic: str = "project-conventions"
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MemoryConsolidationResult:
    candidates: tuple[MemoryCandidate, ...]
    input_count: int
    duplicate_count: int
    rejected_count: int


def consolidate_evidence(
    evidence: Iterable[EpisodicEvidence],
    active_facts: Iterable[MemoryStoredFact],
) -> MemoryConsolidationResult:
    """Create bounded candidates without granting approval or inferring corrections."""

    inputs = tuple(evidence)
    seen = {
        (fact.topic, _normalized(fact.content)) for fact in active_facts if fact.status == "active"
    }
    candidates: list[MemoryCandidate] = []
    duplicates = 0
    rejected = 0
    for item in inputs:
        content = " ".join(item.content.split())
        refs = tuple(dict.fromkeys(ref.strip() for ref in item.evidence_refs if ref.strip()))
        if (
            not item.run_id.strip()
            or not content
            or len(content) > 4_000
            or item.topic not in {"project-conventions", "decisions"}
            or not refs
        ):
            rejected += 1
            continue
        key = (item.topic, _normalized(content))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        candidates.append(
            MemoryCandidate(
                content=content,
                topic=item.topic,
                source="memory_consolidation",
                source_ref=f"{item.run_id}:{','.join(refs[:8])}"[:512],
            )
        )
    return MemoryConsolidationResult(
        candidates=tuple(candidates),
        input_count=len(inputs),
        duplicate_count=duplicates,
        rejected_count=rejected,
    )


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


__all__ = ["EpisodicEvidence", "MemoryConsolidationResult", "consolidate_evidence"]
