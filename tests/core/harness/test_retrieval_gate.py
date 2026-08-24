"""Deterministic H2.8 retrieval routing and public receipts."""

from sage_harness import MemoryReference, MemoryRetrievalResult

from core.coding.run_coordinator import RunEvent
from core.harness.retrieval_gate import (
    decide_retrieval_gate,
    memory_retrieval_events,
    project_retrieval_gate_sources,
    retrieval_source_event,
    retrieval_sources_from_events,
    retrieval_tool_scope_from_events,
)
from core.harness.task_intent import TaskIntentEnvelope


def test_fast_chat_skips_retrieval_without_leaking_query() -> None:
    receipt = decide_retrieval_gate(
        "1 + 1 等于多少？",
        memory_available=True,
        knowledge_available=True,
        web_available=True,
    )

    payload = receipt.to_payload(run_id="run-1")
    assert receipt.decision == "skip"
    assert receipt.reason_code == "no_retrieval_signal"
    assert payload["actual_hit_count"] == 0
    assert "1 + 1" not in repr(payload)
    assert len(receipt.query_fingerprint) == 16


def test_explicit_sources_select_mixed_and_degrade_truthfully() -> None:
    receipt = decide_retrieval_gate(
        "结合我的知识库和官网最新资料解释 LangGraph checkpoint",
        memory_available=True,
        knowledge_available=True,
        web_available=False,
    )

    assert receipt.decision == "knowledge"
    assert receipt.candidate_sources == ("knowledge", "web")
    assert receipt.selected_sources == ("knowledge",)
    assert receipt.degraded is True
    assert receipt.token_budget_by_source == {"knowledge": 3_000}


def test_policy_source_projection_keeps_receipt_fields_consistent() -> None:
    original = decide_retrieval_gate(
        "只搜索官网最新资料",
        memory_available=False,
        knowledge_available=True,
        web_available=True,
    )

    projected = project_retrieval_gate_sources(
        original,
        selected_sources=("knowledge",),
        reason_code="learning_scope_source_policy",
    )

    assert projected.decision == "knowledge"
    assert projected.selected_sources == ("knowledge",)
    assert set(projected.selected_sources).issubset(projected.candidate_sources)
    assert projected.token_budget_by_source == {"knowledge": 3_000}
    assert projected.degraded is True


def test_memory_signal_selects_only_approved_memory_channel() -> None:
    receipt = decide_retrieval_gate(
        "你还记得我之前告诉过你的个人偏好吗？",
        memory_available=True,
        knowledge_available=False,
        web_available=False,
    )

    assert receipt.decision == "semantic_memory"
    assert receipt.memory_selected is True
    assert receipt.selected_sources == ("semantic_memory",)


def test_frozen_knowledge_selection_routes_to_knowledge() -> None:
    receipt = decide_retrieval_gate(
        "帮我解释这里",
        surface_context={
            "selection": {"type": "graph_node", "id": "node-1"},
            "graph_revision": "graph-7",
        },
        memory_available=True,
        knowledge_available=True,
        web_available=False,
    )

    assert receipt.decision == "knowledge"
    assert receipt.reason_code == "explicit_source_signal"


def test_knowledge_retrieval_word_routes_to_knowledge() -> None:
    receipt = decide_retrieval_gate(
        "检索 Phoenix checkpoint 的当前修订证据",
        memory_available=True,
        knowledge_available=True,
        web_available=True,
    )

    assert receipt.decision == "knowledge"
    assert receipt.selected_sources == ("knowledge",)


def test_plain_learning_question_uses_learning_intent_to_select_knowledge() -> None:
    receipt = decide_retrieval_gate(
        "久期为什么会随着利率变化？",
        memory_available=True,
        knowledge_available=True,
        web_available=True,
    )

    assert receipt.decision == "knowledge"
    assert receipt.reason_code == "learning_intent_signal"
    assert receipt.intent_route.intent_family == "explain"
    assert receipt.intent_route.knowledge_scope == "workspace"
    assert receipt.intent_route.recommended_mode == "single_pass"


def test_complex_book_comparison_marks_agentic_candidate_without_exposing_ids() -> None:
    receipt = decide_retrieval_gate(
        "比较这两本书对风险的定义",
        surface_context={"book_context": {"book_ids": ["private-a", "private-b"]}},
        memory_available=True,
        knowledge_available=True,
        web_available=True,
    )

    payload = receipt.to_payload(run_id="run-learning")
    assert receipt.selected_sources == ("knowledge",)
    assert receipt.intent_route.recommended_mode == "agentic_candidate"
    assert payload["intent_route"]["candidate_book_count"] == 2  # type: ignore[index]
    assert "private-a" not in repr(payload)


def test_no_tools_clears_explicit_knowledge_candidates() -> None:
    receipt = decide_retrieval_gate(
        "根据知识库解释久期，但不要调用任何工具",
        memory_available=True,
        knowledge_available=True,
        web_available=True,
    )

    assert receipt.decision == "skip"
    assert receipt.selected_sources == ()
    assert receipt.tool_scope == "no_tools"


def test_no_web_wins_over_a_conflicting_web_only_request() -> None:
    receipt = decide_retrieval_gate(
        "只搜索官网的利率数据，但不要联网",
        memory_available=True,
        knowledge_available=True,
        web_available=True,
    )

    assert "web" not in receipt.selected_sources
    assert "web_forbidden" in receipt.intent_route.risk_flags


def test_ephemeral_history_only_request_suppresses_negated_web_and_all_tools() -> None:
    receipt = decide_retrieval_gate(
        "上次 shell 审批恢复那轮做了什么？只根据历史运行记录概括，不要联网。",
        memory_available=True,
        knowledge_available=True,
        web_available=True,
    )

    assert receipt.decision == "episodic_memory"
    assert receipt.candidate_sources == ("episodic_memory",)
    assert receipt.selected_sources == ("episodic_memory",)
    assert receipt.tool_scope == "retrieval_only"


def test_knowledge_only_request_freezes_retrieval_tool_scope_without_web() -> None:
    receipt = decide_retrieval_gate(
        "只检索 Sage 知识库中的 checkpoint 资料，不联网；若没有证据直接说明。",
        memory_available=True,
        knowledge_available=True,
        web_available=True,
    )

    assert receipt.decision == "knowledge"
    assert receipt.selected_sources == ("knowledge",)
    assert receipt.tool_scope == "retrieval_only"


def test_explicit_web_only_request_overrides_other_retrieval_signals() -> None:
    receipt = decide_retrieval_gate(
        "只搜索官网的 checkpoint 资料，返回来源与引用。",
        surface_context={"selection": {"type": "graph_node", "id": "node-1"}},
        memory_available=True,
        knowledge_available=True,
        web_available=True,
    )

    assert receipt.candidate_sources == ("web",)
    assert receipt.selected_sources == ("web",)
    assert receipt.decision == "web"


def test_explicit_no_tool_request_freezes_empty_tool_scope() -> None:
    receipt = decide_retrieval_gate(
        "1 + 1 等于多少？不要调用任何工具，只回答结果。",
        memory_available=True,
        knowledge_available=True,
        web_available=True,
    )

    assert receipt.decision == "skip"
    assert receipt.tool_scope == "no_tools"


def test_intent_envelope_can_only_narrow_retrieval_candidates() -> None:
    envelope = TaskIntentEnvelope(
        intent_kind="research",
        requested_effects=("read",),
        capability_hints=("knowledge",),
    )

    receipt = decide_retrieval_gate(
        "结合知识库和官网最新资料",
        memory_available=True,
        knowledge_available=True,
        web_available=True,
        intent_envelope=envelope,
    )

    assert receipt.candidate_sources == ("knowledge",)
    assert receipt.selected_sources == ("knowledge",)
    assert receipt.reason_code == "intent_capability_scope"
    assert envelope.can_only_narrow_capabilities is True


def test_research_without_source_hint_preserves_existing_retrieval_candidates() -> None:
    envelope = TaskIntentEnvelope(
        intent_kind="research",
        requested_effects=("read",),
    )

    receipt = decide_retrieval_gate(
        "检索 Phoenix checkpoint 的当前修订证据",
        memory_available=True,
        knowledge_available=True,
        web_available=False,
        intent_envelope=envelope,
    )

    assert receipt.candidate_sources == ("knowledge",)
    assert receipt.selected_sources == ("knowledge",)
    assert receipt.reason_code == "explicit_source_signal"


def test_retrieval_result_projects_actual_hits_without_content() -> None:
    event = RunEvent(
        kind="tool",
        status="completed",
        payload={
            "type": "tool_result",
            "tool": "knowledge_search",
            "content": (
                '{"status":"evidence_found","used_tokens":120,"token_budget":3000,'
                '"omitted_count":2,"citations":[{"citation_id":"secret"}]}'
            ),
        },
        event_id="tool-result-1",
    )

    projected = retrieval_source_event(event, run_id="run-1")

    assert projected is not None
    assert projected.payload == {
        "type": "retrieval_source_completed",
        "version": 1,
        "run_id": "run-1",
        "source": "knowledge",
        "status": "evidence_found",
        "actual_hit_count": 1,
        "used_tokens": 120,
        "token_budget": 3000,
        "omitted_count": 2,
    }
    assert "secret" not in repr(projected.payload)


def test_memory_retrieval_projects_per_source_receipts_without_content() -> None:
    result = MemoryRetrievalResult(
        references=(
            MemoryReference(
                memory_id="memory-secret",
                summary="private preference",
                metadata={"memory_kind": "semantic"},
            ),
        ),
        token_budget_by_source={"semantic_memory": 1200, "episodic_memory": 1600},
        used_tokens_by_source={"semantic_memory": 12, "episodic_memory": 0},
        omitted_count_by_source={"semantic_memory": 1, "episodic_memory": 0},
    )

    events = memory_retrieval_events(result, run_id="run-1")

    assert [event.payload["source"] for event in events] == [
        "semantic_memory",
        "episodic_memory",
    ]
    assert events[0].payload["status"] == "evidence_found"
    assert events[0].payload["actual_hit_count"] == 1
    assert events[1].payload["status"] == "no_evidence"
    assert "private preference" not in repr(events)


def test_retrieval_sources_restore_explicit_skip_and_legacy_absence() -> None:
    skip = RunEvent(
        kind="harness",
        status="completed",
        payload={
            "type": "retrieval_gate_decided",
            "selected_sources": [],
        },
    )
    mixed = RunEvent(
        kind="harness",
        status="completed",
        payload={
            "type": "retrieval_gate_decided",
            "selected_sources": ["knowledge", "web", "forged"],
        },
    )

    assert retrieval_sources_from_events(()) is None
    assert retrieval_sources_from_events((skip,)) == frozenset()
    assert retrieval_sources_from_events((skip, mixed)) == frozenset({"knowledge", "web"})
    assert retrieval_tool_scope_from_events(()) is None
    assert retrieval_tool_scope_from_events((skip,)) == "default"

    strict = RunEvent(
        kind="harness",
        status="completed",
        payload={
            "type": "retrieval_gate_decided",
            "selected_sources": ["knowledge"],
            "tool_scope": "retrieval_only",
        },
    )
    assert retrieval_tool_scope_from_events((skip, strict)) == "retrieval_only"
