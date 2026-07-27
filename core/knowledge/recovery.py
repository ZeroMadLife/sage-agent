"""Bounded, auditable recovery for low-occupancy knowledge retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from core.knowledge.retrieval import KnowledgeRetrievalMode, KnowledgeSearchHit

KnowledgeRecoveryStatus = Literal[
    "disabled",
    "not_needed",
    "not_available",
    "recovered",
    "not_improved",
    "exhausted",
]
KnowledgeRecoveryTrigger = Literal["initial", "insufficient_results"]
KnowledgeNoEvidenceReason = Literal[
    "recovery_disabled",
    "no_rewrite_available",
    "bounded_recovery_exhausted",
]


@dataclass(frozen=True, slots=True)
class KnowledgeRecoveryPolicy:
    """Hard limits for one initial retrieval plus at most one retry."""

    enabled: bool = False
    max_rounds: int = 2
    min_results: int = 4
    top_k_multiplier: int = 2
    max_top_k: int = 20

    def __post_init__(self) -> None:
        if isinstance(self.max_rounds, bool) or self.max_rounds not in {1, 2}:
            raise ValueError("Knowledge recovery allows only one or two rounds")
        if isinstance(self.min_results, bool) or not 1 <= self.min_results <= 20:
            raise ValueError("Knowledge recovery min_results must be between 1 and 20")
        if isinstance(self.top_k_multiplier, bool) or not 1 <= self.top_k_multiplier <= 4:
            raise ValueError("Knowledge recovery top_k_multiplier must be between 1 and 4")
        if isinstance(self.max_top_k, bool) or not 1 <= self.max_top_k <= 50:
            raise ValueError("Knowledge recovery max_top_k must be between 1 and 50")


@dataclass(frozen=True, slots=True)
class KnowledgeQueryRewrite:
    query: str
    matched_rule_count: int


class KnowledgeQueryRewriter(Protocol):
    rewriter_id: str
    rewriter_revision: str

    def rewrite(self, query: str) -> KnowledgeQueryRewrite | None: ...


class TechnicalGlossaryQueryRewriter:
    """Append bounded English corpus terminology for audited bilingual gaps."""

    rewriter_id = "sage.technical-glossary"
    rewriter_revision = "1.0.0"
    _rules = (
        ("过滤条件", ("filtering",)),
        ("返回不足", ("fewer matching rows", "iterative scans")),
        ("过滤列", ("filter columns",)),
        ("命中比例很低", ("selective",)),
        ("精确索引", ("exact indexes",)),
        ("近似索引", ("approximate indexes",)),
        ("继续扫描", ("iterative scans",)),
    )

    def __init__(self, *, max_query_chars: int = 2_000, max_expansions: int = 12) -> None:
        if not 64 <= max_query_chars <= 2_000:
            raise ValueError("Knowledge rewrite max_query_chars must be between 64 and 2000")
        if not 1 <= max_expansions <= 32:
            raise ValueError("Knowledge rewrite max_expansions must be between 1 and 32")
        self.max_query_chars = max_query_chars
        self.max_expansions = max_expansions

    def rewrite(self, query: str) -> KnowledgeQueryRewrite | None:
        normalized = query.strip()
        folded = normalized.casefold()
        matched_rule_count = 0
        expansions: list[str] = []
        seen: set[str] = set()
        for trigger, phrases in self._rules:
            if trigger.casefold() not in folded:
                continue
            matched_rule_count += 1
            for phrase in phrases:
                key = phrase.casefold()
                if key in folded or key in seen or len(expansions) >= self.max_expansions:
                    continue
                seen.add(key)
                expansions.append(phrase)
        if not expansions:
            return None
        selected: list[str] = []
        for phrase in expansions:
            candidate = " ".join((normalized, *selected, phrase))
            if len(candidate) > self.max_query_chars:
                break
            selected.append(phrase)
        if not selected:
            return None
        return KnowledgeQueryRewrite(
            query=" ".join((normalized, *selected)),
            matched_rule_count=matched_rule_count,
        )


@dataclass(frozen=True, slots=True)
class KnowledgeRecoveryAttempt:
    round_index: int
    trigger_reason: KnowledgeRecoveryTrigger
    retrieval_mode: KnowledgeRetrievalMode
    top_k: int
    result_count: int
    query_rewritten: bool


@dataclass(frozen=True, slots=True)
class KnowledgeRecoveryOutcome:
    hits: tuple[KnowledgeSearchHit, ...]
    status: KnowledgeRecoveryStatus
    attempts: tuple[KnowledgeRecoveryAttempt, ...]
    no_evidence_reason: KnowledgeNoEvidenceReason | None = None

    @property
    def round_count(self) -> int:
        return len(self.attempts)


__all__ = [
    "KnowledgeNoEvidenceReason",
    "KnowledgeQueryRewrite",
    "KnowledgeQueryRewriter",
    "KnowledgeRecoveryAttempt",
    "KnowledgeRecoveryOutcome",
    "KnowledgeRecoveryPolicy",
    "KnowledgeRecoveryStatus",
    "KnowledgeRecoveryTrigger",
    "TechnicalGlossaryQueryRewriter",
]
