from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.knowledge.recovery import (
    KnowledgeRecoveryPolicy,
    TechnicalGlossaryQueryRewriter,
)
from core.knowledge.store import KnowledgeStore


def test_glossary_rewriter_is_bounded_deterministic_and_not_case_specific() -> None:
    rewriter = TechnicalGlossaryQueryRewriter()
    query = "为什么 HNSW 加过滤条件后可能返回不足 top-k？"

    first = rewriter.rewrite(query)
    repeated = rewriter.rewrite(query)

    assert repeated == first
    assert first is not None
    assert first.query.startswith(query)
    assert "filtering" in first.query
    assert "fewer matching rows" in first.query
    assert "iterative scans" in first.query
    assert first.matched_rule_count == 2
    assert len(first.query) <= 2_000
    assert rewriter.rewrite("LangGraph 怎样恢复 checkpoint？") is None


def test_recovery_policy_rejects_more_than_two_rounds() -> None:
    with pytest.raises(ValueError, match="one or two"):
        KnowledgeRecoveryPolicy(enabled=True, max_rounds=3)


def test_store_recovery_runs_at_most_twice_and_prefers_better_second_round(
    tmp_path: Path,
) -> None:
    store = KnowledgeStore(
        tmp_path / "workspace",
        tmp_path / "knowledge.sqlite3",
        {},
        recovery_policy=KnowledgeRecoveryPolicy(
            enabled=True,
            min_results=4,
            top_k_multiplier=2,
            max_top_k=20,
        ),
    )
    calls: list[dict[str, Any]] = []

    def search(query: str, **kwargs: Any) -> tuple[str, ...]:
        calls.append({"query": query, **kwargs})
        return ("wrong-1", "wrong-2", "wrong-3") if len(calls) == 1 else ("gold",) * 8

    store.search = search  # type: ignore[method-assign]
    original = "为什么 HNSW 加过滤条件后可能返回不足 top-k？"

    outcome = store.search_with_recovery(original, top_k=8)

    assert len(calls) == 2
    assert calls[0]["round_index"] == 1
    assert calls[0]["trace_query"] == original
    assert calls[0]["rewrite"] is None
    assert calls[1]["round_index"] == 2
    assert calls[1]["top_k"] == 16
    assert calls[1]["trace_query"] == original
    assert calls[1]["rewrite"] == calls[1]["query"]
    assert outcome.hits == ("gold",) * 8
    assert outcome.status == "recovered"
    assert outcome.round_count == 2
    assert outcome.attempts[1].trigger_reason == "insufficient_results"


def test_recovery_does_not_retry_without_a_bounded_rewrite(tmp_path: Path) -> None:
    store = KnowledgeStore(
        tmp_path / "workspace",
        tmp_path / "knowledge.sqlite3",
        {},
        recovery_policy=KnowledgeRecoveryPolicy(enabled=True),
    )
    calls: list[str] = []

    def search(query: str, **_kwargs: Any) -> tuple[str, ...]:
        calls.append(query)
        return ()

    store.search = search  # type: ignore[method-assign]

    outcome = store.search_with_recovery("没有命中术语的空查询", top_k=8)

    assert calls == ["没有命中术语的空查询"]
    assert outcome.status == "not_available"
    assert outcome.no_evidence_reason == "no_rewrite_available"
    assert outcome.round_count == 1


def test_recovery_respects_small_requested_top_k(tmp_path: Path) -> None:
    store = KnowledgeStore(
        tmp_path / "workspace",
        tmp_path / "knowledge.sqlite3",
        {},
        recovery_policy=KnowledgeRecoveryPolicy(enabled=True, min_results=4),
    )
    calls: list[str] = []

    def search(query: str, **_kwargs: Any) -> tuple[str, ...]:
        calls.append(query)
        return ("one",)

    store.search = search  # type: ignore[method-assign]

    outcome = store.search_with_recovery("HNSW 过滤条件", top_k=1)

    assert calls == ["HNSW 过滤条件"]
    assert outcome.status == "not_needed"
    assert outcome.round_count == 1
