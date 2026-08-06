from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.harness.learning_intent import (
    DeterministicLearningIntentRouter,
    LearningIntentRoute,
    resolve_learning_intent,
)


def test_plain_learning_question_routes_to_workspace_without_requiring_rag_keywords() -> None:
    route = DeterministicLearningIntentRouter().route(
        "久期为什么会随着利率变化？",
        surface_context=None,
        thread_goal=None,
    )

    assert route.intent_family == "explain"
    assert route.knowledge_scope == "workspace"
    assert route.depth == "direct"
    assert route.learning_stage == "understand"
    assert route.recommended_mode == "single_pass"
    assert route.routing_confidence >= 0.8


def test_compare_books_is_an_agentic_candidate_but_preserves_book_scope() -> None:
    route = DeterministicLearningIntentRouter().route(
        "比较《投资学基础》和《金融学导论》对风险的定义",
        surface_context={"book_context": {"book_ids": ["book-a", "book-b"]}},
        thread_goal=None,
    )

    assert route.intent_family == "compare"
    assert route.knowledge_scope == "book"
    assert route.depth == "multi_hop"
    assert route.recommended_mode == "agentic_candidate"
    assert route.candidate_book_ids == ("book-a", "book-b")


def test_explicit_no_web_is_a_hard_routing_constraint() -> None:
    route = DeterministicLearningIntentRouter().route(
        "不要联网，只根据本地资料研究久期和凸性的关系",
        surface_context=None,
        thread_goal=None,
    )

    assert route.knowledge_scope == "workspace"
    assert "web_forbidden" in route.risk_flags
    assert route.recommended_mode == "agentic_candidate"


def test_public_projection_does_not_expose_topics_or_book_ids() -> None:
    route = LearningIntentRoute(
        intent_family="compare",
        knowledge_scope="book",
        candidate_topics=("private-topic",),
        candidate_book_ids=("private-book",),
        candidate_chapter_ids=("private-chapter",),
        depth="multi_hop",
        learning_stage="understand",
        risk_flags=("explicit_book_scope",),
        routing_confidence=0.9,
        recommended_mode="agentic_candidate",
        provider_id="test.router",
        provider_revision="1",
    )

    payload = route.to_public_payload()

    assert payload["candidate_topic_count"] == 1
    assert payload["candidate_book_count"] == 1
    assert payload["candidate_chapter_count"] == 1
    assert "private" not in repr(payload)


class _FailingRouter:
    provider_id = "test.failing"
    provider_revision = "1"

    def route(self, user_message, *, surface_context, thread_goal):  # type: ignore[no-untyped-def]
        del user_message, surface_context, thread_goal
        raise RuntimeError("router unavailable")


class _UnsafeWebRouter:
    provider_id = "test.unsafe-web"
    provider_revision = "1"

    def route(self, user_message, *, surface_context, thread_goal):  # type: ignore[no-untyped-def]
        del user_message, surface_context, thread_goal
        return LearningIntentRoute(
            intent_family="research",
            knowledge_scope="web",
            depth="multi_hop",
            learning_stage="understand",
            routing_confidence=0.99,
            recommended_mode="agentic_candidate",
            provider_id=self.provider_id,
            provider_revision=self.provider_revision,
        )


def test_router_failure_falls_back_to_deterministic_contract() -> None:
    route = resolve_learning_intent(
        "解释什么是复利",
        surface_context=None,
        thread_goal=None,
        router=_FailingRouter(),
    )

    assert route.intent_family == "explain"
    assert route.provider_id == "sage.deterministic-learning-intent"
    assert "router_fallback" in route.risk_flags


def test_explicit_no_web_overrides_a_model_router() -> None:
    route = resolve_learning_intent(
        "不要联网，研究久期和凸性的关系",
        surface_context=None,
        thread_goal=None,
        router=_UnsafeWebRouter(),
    )

    assert route.knowledge_scope == "workspace"
    assert "web_forbidden" in route.risk_flags


def test_route_schema_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 1"):
        LearningIntentRoute(
            intent_family="explain",
            knowledge_scope="workspace",
            depth="direct",
            learning_stage="understand",
            routing_confidence=1.1,
            recommended_mode="single_pass",
            provider_id="test.router",
            provider_revision="1",
        )
