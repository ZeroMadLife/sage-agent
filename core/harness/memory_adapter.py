"""Proposal-only adapter from the reusable Harness memory port to Sage storage."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from typing import Literal, cast

from sage_harness import (
    MemoryPort,
    MemoryProposalReceipt,
    MemoryReference,
    MemoryRetrievalResult,
)

from core.coding.memory import MemoryCandidate
from core.coding.memory.durable import MemoryFact
from core.coding.persistence.session_event_journal import (
    SessionEvent,
    SessionEventJournal,
    SessionEventJournalError,
)
from core.coding.runtime import CodingRuntime
from core.harness.web_evidence import estimated_tokens

_MAX_PROPOSAL_CHARS = 4_000
_MAX_CONTEXT_REFS = 32
_TOPICS = frozenset({"project-conventions", "decisions"})
_MEMORY_SOURCES = frozenset({"semantic_memory", "episodic_memory"})
_WORD_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{2,}|[\u4e00-\u9fff]+")
_CONFLICT_PIVOTS = frozenset(
    {"default", "prefer", "preference", "should", "mode", "默认", "偏好", "应该", "模式", "使用"}
)
_NEGATION_MARKERS = ("不", "不要", "禁止", "关闭", "禁用", "never", "not ", "avoid", "disable")


@dataclass(frozen=True, slots=True)
class _RankedReference:
    reference: MemoryReference
    score: int
    terms: frozenset[str]


@dataclass(frozen=True, slots=True)
class _ReferenceUnit:
    references: tuple[MemoryReference, ...]
    token_cost: int


class CodingMemoryPort(MemoryPort):
    """Keep model-selected facts pending until Sage's existing CAS approval."""

    def __init__(self, runtime: CodingRuntime) -> None:
        self.runtime = runtime

    async def query_context(
        self,
        thread_id: str,
        query: str,
        *,
        token_budget_by_source: Mapping[str, int],
        current_run_id: str = "",
    ) -> MemoryRetrievalResult:
        """Retrieve approved facts and bounded prior-run episodes for one turn."""
        self._require_thread(thread_id)
        budgets = _validated_budgets(token_budget_by_source)
        query_terms = _lexical_terms(query)
        candidates_by_source: dict[str, list[_RankedReference]] = {}
        unavailable: list[str] = []

        for source in ("semantic_memory", "episodic_memory"):
            budget = budgets.get(source)
            if budget is None:
                continue
            try:
                ranked = (
                    self._semantic_candidates(query_terms)
                    if source == "semantic_memory"
                    else self._episodic_candidates(query_terms, current_run_id=current_run_id)
                )
            except (OSError, SessionEventJournalError, sqlite3.Error):
                candidates_by_source[source] = []
                unavailable.append(source)
                continue
            candidates_by_source[source] = ranked

        references, used_tokens, omitted_counts = _select_bounded_references(
            candidates_by_source,
            budgets,
        )

        return MemoryRetrievalResult(
            references=tuple(references),
            token_budget_by_source=budgets,
            used_tokens_by_source=used_tokens,
            omitted_count_by_source=omitted_counts,
            unavailable_sources=tuple(unavailable),
        )

    async def load_context(
        self,
        thread_id: str,
        *,
        token_budget: int,
    ) -> tuple[MemoryReference, ...]:
        if token_budget < 1:
            raise ValueError("token_budget must be positive")
        result = await self.query_context(
            thread_id,
            "",
            token_budget_by_source={"semantic_memory": token_budget},
        )
        return result.references

    def _semantic_candidates(self, query_terms: frozenset[str]) -> list[_RankedReference]:
        facts = _deduplicated_facts(self.runtime)
        ranked: list[_RankedReference] = []
        for recency, fact in enumerate(reversed(facts), start=1):
            summary = " ".join(str(fact.content).split())[:500]
            if not summary:
                continue
            terms = _lexical_terms(summary)
            overlap = len(query_terms.intersection(terms))
            if query_terms and overlap == 0:
                continue
            identity = "\0".join(
                (str(fact.topic), str(fact.source_ref), str(fact.created_at), summary)
            )
            ranked.append(
                _RankedReference(
                    reference=MemoryReference(
                        memory_id="memory_"
                        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
                        summary=summary,
                        revision=str(fact.created_at)[:80],
                        metadata={
                            "memory_kind": "semantic",
                            "topic": str(fact.topic)[:120],
                            "created_at": str(fact.created_at)[:80],
                            "provenance": "approved_memory",
                            "source_ref": str(fact.source_ref)[:160],
                        },
                    ),
                    score=(overlap * 1_000) + max(0, 100 - recency),
                    terms=terms,
                )
            )
        ranked.sort(key=lambda item: (item.score, item.reference.revision), reverse=True)
        return _mark_conflicts(ranked)

    def _episodic_candidates(
        self,
        query_terms: frozenset[str],
        *,
        current_run_id: str,
    ) -> list[_RankedReference]:
        journal = SessionEventJournal(self.runtime.storage_root, self.runtime.session_id)
        events = journal.replay_before(limit=500).items
        runs: dict[str, list[SessionEvent]] = {}
        for event in events:
            if event.run_id in {current_run_id, "thread-goal"}:
                continue
            runs.setdefault(event.run_id, []).append(event)

        ranked: list[_RankedReference] = []
        for recency, (run_id, run_events) in enumerate(reversed(list(runs.items())), start=1):
            terminal = next(
                (event for event in reversed(run_events) if event.kind == "terminal"),
                None,
            )
            user_event = next(
                (
                    event
                    for event in run_events
                    if event.kind == "user" and isinstance(event.payload.get("content"), str)
                ),
                None,
            )
            if terminal is None or user_event is None:
                continue
            user_intent = " ".join(str(user_event.payload["content"]).split())[:2_000]
            tool_names = tuple(
                dict.fromkeys(
                    str(event.payload.get("tool", "")).strip()
                    for event in run_events
                    if event.payload.get("type") in {"tool_call", "tool_result"}
                    and str(event.payload.get("tool", "")).strip()
                )
            )
            evidence_refs = _episode_evidence_refs(run_events)
            summary = (
                f"历史运行结果：{terminal.status}"
                + (f"；工具：{', '.join(tool_names[:6])}" if tool_names else "")
                + (f"；证据：{len(evidence_refs)} 项" if evidence_refs else "")
            )[:500]
            terms = _lexical_terms(user_intent).union(_lexical_terms(summary))
            overlap = len(query_terms.intersection(terms))
            if query_terms and overlap == 0:
                continue
            identity = f"{run_id}\0{terminal.sequence}\0{terminal.timestamp}"
            ranked.append(
                _RankedReference(
                    reference=MemoryReference(
                        memory_id="episode_"
                        + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24],
                        summary=summary,
                        revision=f"sequence:{terminal.sequence}",
                        metadata={
                            "memory_kind": "episodic",
                            "topic": "run_episode",
                            "created_at": terminal.timestamp[:80],
                            "provenance": "durable_timeline",
                            "source_ref": run_id[:160],
                            "run_id": run_id[:160],
                            "evidence_refs": ",".join(evidence_refs[:8])[:1_024],
                            "query_fingerprint": hashlib.sha256(
                                user_intent.encode("utf-8")
                            ).hexdigest()[:16],
                        },
                    ),
                    score=(overlap * 1_000) + max(0, 100 - recency),
                    terms=terms,
                )
            )
        ranked.sort(key=lambda item: (item.score, item.reference.revision), reverse=True)
        return ranked

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


def _validated_budgets(value: Mapping[str, int]) -> dict[str, int]:
    budgets: dict[str, int] = {}
    for source, budget in value.items():
        if source not in _MEMORY_SOURCES:
            raise ValueError(f"unsupported memory source: {source}")
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 1:
            raise ValueError("memory source token budgets must be positive integers")
        budgets[source] = min(budget, 100_000)
    return budgets


def _deduplicated_facts(runtime: CodingRuntime) -> list[MemoryFact]:
    facts: list[MemoryFact] = []
    seen: set[tuple[str, str]] = set()
    approved = runtime.memory_manager.memory_store.list_facts()
    for candidate in approved:
        key = (candidate.topic, " ".join(candidate.content.split()).casefold())
        if key in seen:
            continue
        seen.add(key)
        facts.append(
            MemoryFact(
                topic=candidate.topic,
                content=candidate.content,
                source=candidate.source,
                source_ref=candidate.source_ref,
                created_at=candidate.created_at,
                status="active",
            )
        )
    for fact in runtime.memory_manager.list_facts():
        key = (fact.topic, " ".join(fact.content.split()).casefold())
        if key in seen:
            continue
        seen.add(key)
        facts.append(fact)
    return facts


def _lexical_terms(value: str) -> frozenset[str]:
    terms: set[str] = set()
    for match in _WORD_PATTERN.finditer(value.casefold()[:8_000]):
        token = match.group(0)
        if token.isascii():
            terms.add(token)
            continue
        if len(token) == 1:
            terms.add(token)
            continue
        terms.update(token[index : index + 2] for index in range(len(token) - 1))
    return frozenset(terms)


def _mark_conflicts(items: Sequence[_RankedReference]) -> list[_RankedReference]:
    adjacency: dict[str, set[str]] = {}
    for left_index, left in enumerate(items):
        left_topic = str(left.reference.metadata.get("topic", ""))
        for right in items[left_index + 1 :]:
            if left_topic != str(right.reference.metadata.get("topic", "")):
                continue
            if not _is_potential_conflict(left, right):
                continue
            left_id = left.reference.memory_id
            right_id = right.reference.memory_id
            adjacency.setdefault(left_id, set()).add(right_id)
            adjacency.setdefault(right_id, set()).add(left_id)

    membership: dict[str, str] = {}
    visited: set[str] = set()
    for root in sorted(adjacency):
        if root in visited:
            continue
        pending = [root]
        members: set[str] = set()
        while pending:
            current = pending.pop()
            if current in members:
                continue
            members.add(current)
            pending.extend(adjacency.get(current, ()))
        visited.update(members)
        group_id = (
            "conflict_"
            + hashlib.sha256("\0".join(sorted(members)).encode("utf-8")).hexdigest()[:16]
        )
        for memory_id in members:
            membership[memory_id] = group_id

    marked: list[_RankedReference] = []
    for item in items:
        conflict_group = membership.get(item.reference.memory_id, "")
        metadata = dict(item.reference.metadata)
        if conflict_group:
            metadata["conflict"] = "true"
            metadata["conflict_group"] = conflict_group
            metadata["conflict_kind"] = "potential_lexical"
        marked.append(
            _RankedReference(
                reference=MemoryReference(
                    memory_id=item.reference.memory_id,
                    summary=item.reference.summary,
                    revision=item.reference.revision,
                    metadata=metadata,
                ),
                score=item.score,
                terms=item.terms,
            )
        )
    return marked


def _has_negation(value: str) -> bool:
    lowered = value.casefold()
    return any(marker in lowered for marker in _NEGATION_MARKERS)


def _is_potential_conflict(left: _RankedReference, right: _RankedReference) -> bool:
    shared = left.terms.intersection(right.terms)
    if not shared:
        return False
    shared_pivots = shared.intersection(_CONFLICT_PIVOTS)
    if not shared_pivots:
        return False
    smaller = max(1, min(len(left.terms), len(right.terms)))
    if len(shared) / smaller < 0.45:
        return False
    if not left.terms.difference(right.terms) or not right.terms.difference(left.terms):
        return False
    negation_changed = _has_negation(left.reference.summary) != _has_negation(
        right.reference.summary
    )
    return negation_changed or len(shared_pivots) >= 2


def _apply_budget(
    items: Sequence[_RankedReference],
    token_budget: int,
) -> tuple[list[_ReferenceUnit], int]:
    remaining = token_budget
    selected: list[_ReferenceUnit] = []
    omitted = 0
    for unit in _reference_units(items):
        if unit.token_cost > remaining:
            omitted += len(unit.references)
            continue
        selected.append(unit)
        remaining -= unit.token_cost
    return selected, omitted


def _select_bounded_references(
    candidates_by_source: Mapping[str, Sequence[_RankedReference]],
    budgets: Mapping[str, int],
) -> tuple[list[MemoryReference], dict[str, int], dict[str, int]]:
    """Apply independent budgets, then fairly share the global reference cap."""
    selected_by_source: dict[str, list[_ReferenceUnit]] = {}
    omitted_by_source: dict[str, int] = {}
    for source in budgets:
        selected, omitted = _apply_budget(
            candidates_by_source.get(source, ()),
            budgets[source],
        )
        selected_by_source[source] = selected
        omitted_by_source[source] = omitted

    combined: list[MemoryReference] = []
    used_tokens = {source: 0 for source in budgets}
    globally_omitted = {source: 0 for source in budgets}
    processed_units = {source: 0 for source in budgets}
    index = 0
    while len(combined) < _MAX_CONTEXT_REFS:
        appended = False
        for source in budgets:
            candidates = selected_by_source[source]
            if index >= len(candidates):
                continue
            unit = candidates[index]
            processed_units[source] += 1
            if len(combined) + len(unit.references) > _MAX_CONTEXT_REFS:
                globally_omitted[source] += len(unit.references)
                appended = True
                continue
            combined.extend(unit.references)
            used_tokens[source] += unit.token_cost
            appended = True
            if len(combined) >= _MAX_CONTEXT_REFS:
                break
        if not appended:
            break
        index += 1

    omitted_counts = {
        source: omitted_by_source[source]
        + globally_omitted[source]
        + sum(
            len(unit.references) for unit in selected_by_source[source][processed_units[source] :]
        )
        for source in budgets
    }
    return combined, used_tokens, omitted_counts


def _reference_units(items: Sequence[_RankedReference]) -> tuple[_ReferenceUnit, ...]:
    grouped: dict[str, list[MemoryReference]] = {}
    order: list[str] = []
    for item in items:
        group = str(item.reference.metadata.get("conflict_group", "")).strip()
        key = group or item.reference.memory_id
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(item.reference)
    return tuple(
        _ReferenceUnit(
            references=tuple(grouped[key]),
            token_cost=sum(_estimated_tokens(reference) for reference in grouped[key]),
        )
        for key in order
    )


def _estimated_tokens(reference: MemoryReference) -> int:
    metadata = reference.metadata
    qualifiers = [
        str(metadata.get("memory_kind", "semantic"))[:40],
        reference.revision[:80],
        str(metadata.get("provenance", ""))[:80],
    ]
    qualifier_text = ", ".join(part for part in qualifiers if part)
    conflict_group = str(metadata.get("conflict_group", ""))[:120]
    conflict = f" conflict={conflict_group}" if conflict_group else ""
    rendered = "\n".join(
        (
            "<sage_durable_context>",
            "## Memory references",
            f"- [{escape(qualifier_text, quote=False)}] "
            f"{escape(reference.memory_id[:120], quote=False)}"
            f"{escape(conflict, quote=False)}: "
            f"{escape(' '.join(reference.summary.split())[:500], quote=False)}",
            "</sage_durable_context>",
        )
    )
    return estimated_tokens(rendered)


def _episode_evidence_refs(events: Sequence[SessionEvent]) -> tuple[str, ...]:
    refs: dict[str, None] = {}
    for event in events:
        raw_refs = event.payload.get("evidence_refs")
        if isinstance(raw_refs, list | tuple):
            for raw in raw_refs:
                reference = str(raw).strip()
                if reference:
                    refs.setdefault(reference[:240], None)
        if event.payload.get("type") != "tool_result":
            continue
        content = event.payload.get("content")
        if not isinstance(content, str):
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, Mapping):
            continue
        for key in ("citations", "evidence"):
            records = payload.get(key)
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, Mapping):
                    continue
                reference = str(
                    record.get("citation_id") or record.get("evidence_ref") or ""
                ).strip()
                if reference:
                    refs.setdefault(reference[:240], None)
    return tuple(refs)[:32]
