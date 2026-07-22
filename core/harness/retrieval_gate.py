"""Deterministic, content-free retrieval routing for one Harness turn."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

from sage_harness import MemoryRetrievalResult

from core.coding.run_coordinator import RunEvent

RetrievalDecision = Literal[
    "skip",
    "semantic_memory",
    "episodic_memory",
    "knowledge",
    "web",
    "mixed",
]
RetrievalToolScope = Literal["default", "retrieval_only", "no_tools"]

_WEB_PATTERN = re.compile(
    r"(?:\bweb\b|\bonline\b|联网|网页|官网|官方资料|最新|近期|今天|当前版本|搜索互联网)",
    re.IGNORECASE,
)
_WEB_NEGATION_PATTERN = re.compile(
    r"(?:不要|不准|禁止|无需)\s*(?:使用|调用|通过|访问|进行)?\s*"
    r"(?:web(?:\s*search)?|online|联网|网页|网络|互联网)|(?:不|无需)\s*联网",
    re.IGNORECASE,
)
_KNOWLEDGE_PATTERN = re.compile(
    r"(?:知识库|知识图谱|节点|wiki|资料库|文档库|代码仓库|项目资料|README|源码|本地文件|检索)",
    re.IGNORECASE,
)
_SEMANTIC_MEMORY_PATTERN = re.compile(
    r"(?:我的偏好|我的习惯|我喜欢|我不喜欢|记住的|长期目标|个人约束|之前告诉过你)",
    re.IGNORECASE,
)
_EPISODIC_MEMORY_PATTERN = re.compile(
    r"(?:上次|之前那次|刚才|本轮|这个会话|之前执行|之前失败|历史会话|我们做过)",
    re.IGNORECASE,
)
_NO_TOOLS_PATTERN = re.compile(
    r"(?:不要|无需|禁止|不准)\s*(?:调用|使用|执行)\s*(?:任何)?\s*工具",
    re.IGNORECASE,
)
_SEMANTIC_ONLY_PATTERN = re.compile(
    r"(?:只|仅)(?:根据|使用|参考|依赖).{0,16}(?:长期记忆|已批准.*记忆|个人偏好)",
    re.IGNORECASE,
)
_EPISODIC_ONLY_PATTERN = re.compile(
    r"(?:只|仅)(?:根据|使用|参考|依赖).{0,16}" r"(?:历史运行记录|历史会话|上次|之前那次|之前执行)",
    re.IGNORECASE,
)
_KNOWLEDGE_ONLY_PATTERN = re.compile(
    r"(?:只|仅)(?:检索|搜索|使用|根据|从).{0,24}"
    r"(?:Sage\s*)?(?:知识库|知识图谱|Wiki|资料库|文档库)",
    re.IGNORECASE,
)
_WEB_ONLY_PATTERN = re.compile(
    r"(?:只|仅)(?:检索|搜索|使用|根据|从).{0,24}"
    r"(?:Web(?:\s*Search)?|网页|官网|官方资料|互联网|网络)",
    re.IGNORECASE,
)

_SOURCE_BUDGETS = {
    "semantic_memory": 1_200,
    "episodic_memory": 1_600,
    "knowledge": 3_000,
    "web": 3_000,
}


@dataclass(frozen=True, slots=True)
class RetrievalGateReceipt:
    """Public routing receipt that never contains the user query or retrieved content."""

    decision: RetrievalDecision
    reason_code: str
    candidate_sources: tuple[str, ...]
    selected_sources: tuple[str, ...]
    available_sources: tuple[str, ...]
    token_budget_by_source: Mapping[str, int]
    query_fingerprint: str
    latency_ms: int
    degraded: bool = False
    tool_scope: RetrievalToolScope = "default"

    @property
    def memory_selected(self) -> bool:
        return any(
            source in {"semantic_memory", "episodic_memory"} for source in self.selected_sources
        )

    def to_payload(self, *, run_id: str) -> dict[str, object]:
        return {
            "type": "retrieval_gate_decided",
            "version": 1,
            "run_id": run_id,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "candidate_sources": list(self.candidate_sources),
            "selected_sources": list(self.selected_sources),
            "available_sources": list(self.available_sources),
            "token_budget_by_source": dict(self.token_budget_by_source),
            "candidate_count": len(self.candidate_sources),
            "actual_hit_count": 0,
            "query_fingerprint": self.query_fingerprint,
            "latency_ms": self.latency_ms,
            "degraded": self.degraded,
            "tool_scope": self.tool_scope,
        }

    def to_context(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "reason_code": self.reason_code,
            "selected_sources": list(self.selected_sources),
            "token_budget_by_source": dict(self.token_budget_by_source),
            "query_fingerprint": self.query_fingerprint,
            "degraded": self.degraded,
            "tool_scope": self.tool_scope,
        }


def decide_retrieval_gate(
    user_message: str,
    *,
    surface_context: Mapping[str, Any] | None = None,
    thread_goal: Mapping[str, Any] | None = None,
    memory_available: bool,
    knowledge_available: bool,
    web_available: bool,
) -> RetrievalGateReceipt:
    """Route explicit retrieval signals before model/tool selection."""
    started_at = time.monotonic()
    normalized = " ".join(user_message.split())[:8_000]
    fingerprint = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]

    available: list[str] = []
    if memory_available:
        available.extend(("semantic_memory", "episodic_memory"))
    if knowledge_available:
        available.append("knowledge")
    if web_available:
        available.append("web")

    candidates: list[str] = []
    if _SEMANTIC_MEMORY_PATTERN.search(normalized):
        candidates.append("semantic_memory")
    if _EPISODIC_MEMORY_PATTERN.search(normalized):
        candidates.append("episodic_memory")
    if _KNOWLEDGE_PATTERN.search(normalized) or _has_knowledge_selection(surface_context):
        candidates.append("knowledge")
    web_negated = bool(_WEB_NEGATION_PATTERN.search(normalized))
    if _WEB_PATTERN.search(normalized) and not web_negated:
        candidates.append("web")
    if thread_goal and not candidates and _goal_requests_retrieval(thread_goal):
        candidates.append("knowledge")

    explicit_source = _explicit_only_source(normalized)
    if explicit_source is not None:
        candidates = [explicit_source]

    candidates = list(dict.fromkeys(candidates))
    selected = [source for source in candidates if source in available]
    degraded = len(selected) != len(candidates)
    if not candidates:
        decision: RetrievalDecision = "skip"
        reason_code = "no_retrieval_signal"
    elif not selected:
        decision = "skip"
        reason_code = "requested_sources_unavailable"
    elif len(selected) > 1:
        decision = "mixed"
        reason_code = "multiple_explicit_sources"
    else:
        decision = cast(RetrievalDecision, selected[0])
        reason_code = "explicit_source_signal"

    budgets = {source: _SOURCE_BUDGETS[source] for source in selected}
    tool_scope = _tool_scope(normalized)
    return RetrievalGateReceipt(
        decision=decision,
        reason_code=reason_code,
        candidate_sources=tuple(candidates),
        selected_sources=tuple(selected),
        available_sources=tuple(available),
        token_budget_by_source=budgets,
        query_fingerprint=fingerprint,
        latency_ms=max(0, round((time.monotonic() - started_at) * 1_000)),
        degraded=degraded,
        tool_scope=tool_scope,
    )


def retrieval_source_event(event: RunEvent, *, run_id: str) -> RunEvent | None:
    """Project actual Knowledge/Web hits from a completed public tool event."""
    if event.payload.get("type") != "tool_result":
        return None
    tool = str(event.payload.get("tool", ""))
    source = {"knowledge_search": "knowledge", "search_web": "web"}.get(tool)
    if source is None:
        return None
    payload = _tool_result_payload(event.payload.get("content"))
    citations = payload.get("citations")
    evidence = payload.get("evidence")
    actual_hit_count = (
        len(citations)
        if isinstance(citations, list)
        else len(evidence)
        if isinstance(evidence, list)
        else 0
    )
    return RunEvent(
        kind="harness",
        status="error" if event.status == "error" else "completed",
        payload={
            "type": "retrieval_source_completed",
            "version": 1,
            "run_id": run_id,
            "source": source,
            "status": str(
                payload.get("status") or ("error" if event.status == "error" else "completed")
            ),
            "actual_hit_count": actual_hit_count,
            "used_tokens": _non_negative_int(payload.get("used_tokens")),
            "token_budget": _non_negative_int(payload.get("token_budget")),
            "omitted_count": _non_negative_int(payload.get("omitted_count")),
        },
        event_id=f"harness:{run_id}:retrieval-source:{source}:{event.event_id or 'result'}",
    )


def memory_retrieval_events(
    result: MemoryRetrievalResult,
    *,
    run_id: str,
) -> tuple[RunEvent, ...]:
    """Project content-free semantic/episodic retrieval receipts."""
    events: list[RunEvent] = []
    unavailable = set(result.unavailable_sources)
    for source, token_budget in result.token_budget_by_source.items():
        expected_kind = {
            "semantic_memory": "semantic",
            "episodic_memory": "episodic",
        }.get(source)
        if expected_kind is None:
            continue
        actual_hit_count = sum(
            1
            for reference in result.references
            if reference.metadata.get("memory_kind") == expected_kind
        )
        status = (
            "unavailable"
            if source in unavailable
            else "evidence_found"
            if actual_hit_count
            else "no_evidence"
        )
        events.append(
            RunEvent(
                kind="harness",
                status="error" if status == "unavailable" else "completed",
                payload={
                    "type": "retrieval_source_completed",
                    "version": 1,
                    "run_id": run_id,
                    "source": source,
                    "status": status,
                    "actual_hit_count": actual_hit_count,
                    "used_tokens": _non_negative_int(result.used_tokens_by_source.get(source)),
                    "token_budget": _non_negative_int(token_budget),
                    "omitted_count": _non_negative_int(result.omitted_count_by_source.get(source)),
                },
                event_id=f"harness:{run_id}:retrieval-source:{source}",
            )
        )
    return tuple(events)


def retrieval_sources_from_events(events: Sequence[object]) -> frozenset[str] | None:
    """Restore the server-owned Gate decision for an interrupted run.

    ``None`` means the run predates Retrieval Gate receipts and keeps the legacy
    compatibility path. An explicit empty set is a persisted ``skip`` decision.
    """
    for event in reversed(events):
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            continue
        if payload.get("type") != "retrieval_gate_decided":
            continue
        raw_sources = payload.get("selected_sources")
        if not isinstance(raw_sources, list | tuple):
            return frozenset()
        return frozenset(str(source) for source in raw_sources if str(source) in _SOURCE_BUDGETS)
    return None


def retrieval_tool_scope_from_events(
    events: Sequence[object],
) -> RetrievalToolScope | None:
    """Restore the frozen per-turn tool scope for approval resume.

    ``None`` preserves the legacy behavior for runs created before the field
    existed. New receipts fail closed to ``default`` when the value is invalid.
    """
    for event in reversed(events):
        payload = getattr(event, "payload", None)
        if not isinstance(payload, Mapping):
            continue
        if payload.get("type") != "retrieval_gate_decided":
            continue
        value = str(payload.get("tool_scope", "default"))
        if value in {"default", "retrieval_only", "no_tools"}:
            return cast(RetrievalToolScope, value)
        return "default"
    return None


def _tool_scope(normalized: str) -> RetrievalToolScope:
    if _NO_TOOLS_PATTERN.search(normalized):
        return "no_tools"
    if any(
        pattern.search(normalized)
        for pattern in (
            _SEMANTIC_ONLY_PATTERN,
            _EPISODIC_ONLY_PATTERN,
            _KNOWLEDGE_ONLY_PATTERN,
            _WEB_ONLY_PATTERN,
        )
    ):
        return "retrieval_only"
    return "default"


def _explicit_only_source(normalized: str) -> str | None:
    for pattern, source in (
        (_SEMANTIC_ONLY_PATTERN, "semantic_memory"),
        (_EPISODIC_ONLY_PATTERN, "episodic_memory"),
        (_KNOWLEDGE_ONLY_PATTERN, "knowledge"),
        (_WEB_ONLY_PATTERN, "web"),
    ):
        if pattern.search(normalized):
            return source
    return None


def _has_knowledge_selection(surface_context: Mapping[str, Any] | None) -> bool:
    if not isinstance(surface_context, Mapping):
        return False
    serialized = json.dumps(surface_context, ensure_ascii=True, sort_keys=True, default=str).lower()
    return any(
        marker in serialized for marker in ("graph_node", "knowledge", "wiki", "page_revision")
    )


def _goal_requests_retrieval(thread_goal: Mapping[str, Any]) -> bool:
    description = " ".join(str(thread_goal.get("description", "")).split())
    return bool(_KNOWLEDGE_PATTERN.search(description) or _WEB_PATTERN.search(description))


def _tool_result_payload(value: object) -> dict[str, Any]:
    if not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


__all__ = [
    "RetrievalGateReceipt",
    "RetrievalToolScope",
    "decide_retrieval_gate",
    "memory_retrieval_events",
    "retrieval_source_event",
    "retrieval_sources_from_events",
    "retrieval_tool_scope_from_events",
]
