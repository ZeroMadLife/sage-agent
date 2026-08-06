"""Structured learning intent routing before retrieval source selection."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

IntentFamily = Literal["explain", "compare", "plan", "practice", "recall", "research", "meta"]
KnowledgeScope = Literal["none", "workspace", "book", "web", "mixed"]
IntentDepth = Literal["direct", "multi_hop"]
LearningStage = Literal["discover", "understand", "apply", "assess"]
RecommendedMode = Literal["skip", "single_pass", "agentic_candidate"]
IntentRiskFlag = Literal[
    "current_information",
    "explicit_book_scope",
    "router_fallback",
    "tools_forbidden",
    "web_forbidden",
]

_COMPARE_PATTERN = re.compile(r"(?:比较|对比|区别|差异|不同|权衡|versus|\bvs\.?\b)", re.I)
_PLAN_PATTERN = re.compile(
    r"(?:学习计划|学习路径|系统学习|从哪里开始|路线怎么安排|学习路线|入门顺序|四周)", re.I
)
_PRACTICE_PATTERN = re.compile(r"(?:练习|出一道|应用题|测验|作业|实战)", re.I)
_RECALL_PATTERN = re.compile(r"(?:闭卷|考考我|回忆|复述|不要给提示|回想)", re.I)
_RESEARCH_PATTERN = re.compile(
    r"(?:调研|研究|查一下|查找|搜索|搜一下|最新|近期|当前政策|research)", re.I
)
_EXPLAIN_PATTERN = re.compile(
    r"(?:解释|什么是|为何|为什么|怎么理解|如何定义|含义|原理|指什么)", re.I
)
_MULTI_HOP_PATTERN = re.compile(r"(?:跨章节|多本|综合|分别|一起理解|关系|影响)", re.I)
_BOOK_PATTERN = re.compile(
    r"(?:这本书|某本书|两本书|本书|这两章|第[一二三四五六七八九十\d]+章|章节|《[^》]{1,80}》)"
)
_WORKSPACE_PATTERN = re.compile(r"(?:知识库|资料库|文档库|本地资料|本地知识|workspace)", re.I)
_WEB_PATTERN = re.compile(
    r"(?:\bweb\b|联网|网页|官网|互联网|最新|近期|今天|当前政策|美联储政策)", re.I
)
_WEB_NEGATION_PATTERN = re.compile(
    r"(?:不要|不准|禁止|无需)\s*(?:使用|调用|通过|访问|进行)?\s*"
    r"(?:web(?:\s*search)?|联网|网页|网络|互联网)|(?:不|无需)\s*联网",
    re.I,
)
_NO_TOOLS_PATTERN = re.compile(
    r"(?:不要|无需|禁止|不准)\s*(?:调用|使用|执行)\s*(?:任何)?\s*工具", re.I
)


class LearningIntentRoute(BaseModel):
    """Revision-bound route contract suitable for a future structured small model."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    intent_family: IntentFamily
    knowledge_scope: KnowledgeScope
    candidate_topics: tuple[str, ...] = ()
    candidate_book_ids: tuple[str, ...] = ()
    candidate_chapter_ids: tuple[str, ...] = ()
    depth: IntentDepth
    learning_stage: LearningStage
    risk_flags: tuple[IntentRiskFlag, ...] = ()
    routing_confidence: float = Field(ge=0.0, le=1.0)
    recommended_mode: RecommendedMode
    abstain_reason: Literal["ambiguous_intent", "router_unavailable"] | None = None
    provider_id: str = Field(min_length=3, max_length=120, pattern=r"^[a-z0-9._-]+$")
    provider_revision: str = Field(min_length=1, max_length=80)

    @field_validator(
        "candidate_topics",
        "candidate_book_ids",
        "candidate_chapter_ids",
    )
    @classmethod
    def _validate_candidates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > 12 or len(value) != len(set(value)):
            raise ValueError("learning intent candidates must be bounded and unique")
        if any(not item or len(item) > 160 for item in value):
            raise ValueError("learning intent candidates must be non-empty and bounded")
        return value

    @field_validator("risk_flags")
    @classmethod
    def _validate_risk_flags(cls, value: tuple[IntentRiskFlag, ...]) -> tuple[IntentRiskFlag, ...]:
        if len(value) != len(set(value)):
            raise ValueError("learning intent risk flags must be unique")
        return value

    def to_public_payload(self) -> dict[str, object]:
        """Project a content-free route receipt for the public timeline."""

        return {
            "version": 1,
            "intent_family": self.intent_family,
            "knowledge_scope": self.knowledge_scope,
            "depth": self.depth,
            "learning_stage": self.learning_stage,
            "recommended_mode": self.recommended_mode,
            "routing_confidence": self.routing_confidence,
            "risk_flags": list(self.risk_flags),
            "abstain_reason": self.abstain_reason,
            "provider_id": self.provider_id,
            "provider_revision": self.provider_revision,
            "candidate_topic_count": len(self.candidate_topics),
            "candidate_book_count": len(self.candidate_book_ids),
            "candidate_chapter_count": len(self.candidate_chapter_ids),
        }

    def to_context(self) -> dict[str, object]:
        return {
            **self.to_public_payload(),
            "candidate_topics": list(self.candidate_topics),
            "candidate_book_ids": list(self.candidate_book_ids),
            "candidate_chapter_ids": list(self.candidate_chapter_ids),
        }


class LearningIntentRouter(Protocol):
    provider_id: str
    provider_revision: str

    def route(
        self,
        user_message: str,
        *,
        surface_context: Mapping[str, Any] | None,
        thread_goal: Mapping[str, Any] | None,
    ) -> LearningIntentRoute: ...


class DeterministicLearningIntentRouter:
    """Low-latency baseline and fail-closed fallback for model-based routing."""

    provider_id = "sage.deterministic-learning-intent"
    provider_revision = "1.0.0"

    def route(
        self,
        user_message: str,
        *,
        surface_context: Mapping[str, Any] | None,
        thread_goal: Mapping[str, Any] | None,
    ) -> LearningIntentRoute:
        normalized = " ".join(user_message.split())[:8_000]
        combined = normalized
        if thread_goal:
            goal = " ".join(str(thread_goal.get("description", "")).split())[:1_000]
            combined = f"{normalized} {goal}".strip()
        intent = _intent_family(combined)
        web_forbidden = bool(_WEB_NEGATION_PATTERN.search(combined))
        tools_forbidden = bool(_NO_TOOLS_PATTERN.search(combined))
        book_scope = bool(_BOOK_PATTERN.search(combined))
        web_signal = bool(_WEB_PATTERN.search(combined)) and not web_forbidden
        workspace_signal = bool(_WORKSPACE_PATTERN.search(combined))
        scope = _knowledge_scope(
            intent,
            tools_forbidden=tools_forbidden,
            book_scope=book_scope,
            web_signal=web_signal,
            workspace_signal=workspace_signal,
        )
        depth: IntentDepth = (
            "multi_hop"
            if intent in {"compare", "plan", "research"}
            or bool(_MULTI_HOP_PATTERN.search(combined))
            else "direct"
        )
        stage = _learning_stage(intent)
        mode: RecommendedMode
        if tools_forbidden or intent == "meta":
            mode = "skip"
        elif depth == "multi_hop" and scope != "none":
            mode = "agentic_candidate"
        else:
            mode = "single_pass"
        flags: list[IntentRiskFlag] = []
        if web_signal:
            flags.append("current_information")
        if book_scope:
            flags.append("explicit_book_scope")
        if tools_forbidden:
            flags.append("tools_forbidden")
        if web_forbidden:
            flags.append("web_forbidden")
        confidence = 0.60 if intent == "meta" else 0.90 if book_scope or workspace_signal else 0.82
        return LearningIntentRoute(
            intent_family=intent,
            knowledge_scope=scope,
            candidate_topics=_surface_values(surface_context, ("topic", "topics")),
            candidate_book_ids=_surface_values(surface_context, ("book_id", "book_ids")),
            candidate_chapter_ids=_surface_values(surface_context, ("chapter_id", "chapter_ids")),
            depth=depth,
            learning_stage=stage,
            risk_flags=tuple(flags),
            routing_confidence=confidence,
            recommended_mode=mode,
            provider_id=self.provider_id,
            provider_revision=self.provider_revision,
        )


def resolve_learning_intent(
    user_message: str,
    *,
    surface_context: Mapping[str, Any] | None,
    thread_goal: Mapping[str, Any] | None,
    router: LearningIntentRouter | None = None,
) -> LearningIntentRoute:
    fallback = DeterministicLearningIntentRouter()
    if router is None:
        return _enforce_hard_constraints(
            user_message,
            fallback.route(
                user_message,
                surface_context=surface_context,
                thread_goal=thread_goal,
            ),
        )
    try:
        route = router.route(
            user_message,
            surface_context=surface_context,
            thread_goal=thread_goal,
        )
        return _enforce_hard_constraints(
            user_message,
            LearningIntentRoute.model_validate(route),
        )
    except Exception:
        route = fallback.route(
            user_message,
            surface_context=surface_context,
            thread_goal=thread_goal,
        )
        return route.model_copy(update={"risk_flags": (*route.risk_flags, "router_fallback")})


def _enforce_hard_constraints(
    user_message: str,
    route: LearningIntentRoute,
) -> LearningIntentRoute:
    """Keep model-based routing subordinate to explicit user constraints."""

    tools_forbidden = bool(_NO_TOOLS_PATTERN.search(user_message))
    web_forbidden = bool(_WEB_NEGATION_PATTERN.search(user_message))
    flags = list(route.risk_flags)
    if tools_forbidden and "tools_forbidden" not in flags:
        flags.append("tools_forbidden")
    if web_forbidden and "web_forbidden" not in flags:
        flags.append("web_forbidden")

    if tools_forbidden:
        return route.model_copy(
            update={
                "knowledge_scope": "none",
                "recommended_mode": "skip",
                "risk_flags": tuple(flags),
            }
        )
    if web_forbidden and route.knowledge_scope in {"web", "mixed"}:
        scope: KnowledgeScope = (
            "book"
            if _BOOK_PATTERN.search(user_message)
            else "none"
            if route.intent_family == "meta"
            else "workspace"
        )
        return route.model_copy(update={"knowledge_scope": scope, "risk_flags": tuple(flags)})
    if tuple(flags) != route.risk_flags:
        return route.model_copy(update={"risk_flags": tuple(flags)})
    return route


def retrieval_sources_for_intent(route: LearningIntentRoute) -> tuple[str, ...]:
    if route.knowledge_scope in {"workspace", "book"}:
        return ("knowledge",)
    if route.knowledge_scope == "web":
        return ("web",)
    if route.knowledge_scope == "mixed":
        return ("knowledge", "web")
    return ()


def _intent_family(text: str) -> IntentFamily:
    for pattern, intent in (
        (_RECALL_PATTERN, "recall"),
        (_PRACTICE_PATTERN, "practice"),
        (_COMPARE_PATTERN, "compare"),
        (_PLAN_PATTERN, "plan"),
        (_RESEARCH_PATTERN, "research"),
        (_EXPLAIN_PATTERN, "explain"),
    ):
        if pattern.search(text):
            return intent  # type: ignore[return-value]
    return "meta"


def _knowledge_scope(
    intent: IntentFamily,
    *,
    tools_forbidden: bool,
    book_scope: bool,
    web_signal: bool,
    workspace_signal: bool,
) -> KnowledgeScope:
    if tools_forbidden:
        return "none"
    if book_scope and web_signal:
        return "mixed"
    if book_scope:
        return "book"
    if workspace_signal and web_signal:
        return "mixed"
    if web_signal:
        return "web"
    if intent in {"explain", "compare", "plan", "practice", "recall", "research"}:
        return "workspace"
    return "none"


def _learning_stage(intent: IntentFamily) -> LearningStage:
    if intent == "plan" or intent == "meta":
        return "discover"
    if intent == "practice":
        return "apply"
    if intent == "recall":
        return "assess"
    return "understand"


def _surface_values(
    context: Mapping[str, Any] | None,
    keys: tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(context, Mapping):
        return ()
    selected: list[str] = []
    pending: list[object] = [context]
    visited = 0
    while pending and visited < 128 and len(selected) < 12:
        current = pending.pop()
        visited += 1
        if isinstance(current, Mapping):
            for key, value in current.items():
                if str(key) in keys:
                    values = (
                        value
                        if isinstance(value, Sequence) and not isinstance(value, str)
                        else (value,)
                    )
                    for item in values:
                        normalized = " ".join(str(item).split())[:160]
                        if normalized and normalized not in selected:
                            selected.append(normalized)
                            if len(selected) >= 12:
                                break
                elif isinstance(value, Mapping | list | tuple):
                    pending.append(value)
        elif isinstance(current, list | tuple):
            pending.extend(current[:32])
    return tuple(selected)


__all__ = [
    "DeterministicLearningIntentRouter",
    "LearningIntentRoute",
    "LearningIntentRouter",
    "resolve_learning_intent",
    "retrieval_sources_for_intent",
]
