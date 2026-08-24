"""Versioned learning-task contracts and deterministic draft policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Literal

LearningStartingLevel = Literal["beginner", "intermediate", "advanced"]
LearningKnowledgePolicy = Literal["preferred", "required", "disabled"]
LearningWebPolicy = Literal["allowed_when_insufficient", "forbidden"]
LearningSourceFreshness = Literal["all", "current"]
LearningRiskClass = Literal["general_education", "financial_education"]
LearningTaskStatus = Literal[
    "draft",
    "activating",
    "active",
    "activation_failed",
    "blocked",
    "completed",
    "archived",
]

FINANCIAL_EDUCATION_NOTICE = "仅提供金融与投资教育，不提供个性化证券买卖建议或收益承诺。"


class UnsetValue:
    __slots__ = ()


UNSET = UnsetValue()


@dataclass(frozen=True, slots=True)
class LearningSourcePolicy:
    knowledge: LearningKnowledgePolicy = "preferred"
    web: LearningWebPolicy = "allowed_when_insufficient"
    domains: tuple[str, ...] = ()
    freshness: LearningSourceFreshness = "all"


@dataclass(frozen=True, slots=True)
class LearningLearnerProfile:
    starting_level: LearningStartingLevel | None = None
    time_budget_minutes_per_week: int | None = None
    target_date: str | None = None


@dataclass(frozen=True, slots=True)
class LearningClarificationQuestion:
    field: str
    prompt: str


@dataclass(frozen=True, slots=True)
class LearningClarification:
    required_fields: tuple[str, ...]
    questions: tuple[LearningClarificationQuestion, ...]
    ready_to_activate: bool


@dataclass(frozen=True, slots=True)
class LearningTask:
    version: int
    workspace_id: str
    task_id: str
    task_revision: int
    template_id: str
    topic: str
    desired_outcome: str | None
    learner_profile: LearningLearnerProfile
    source_policy: LearningSourcePolicy
    risk_class: LearningRiskClass
    risk_notice: str | None
    clarification: LearningClarification
    learning_plan_id: str | None
    learning_plan_hash: str | None
    dag_hash: str | None
    learning_goal_ref: dict[str, str] | None
    status: LearningTaskStatus
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class LearningTaskCreate:
    topic: str
    desired_outcome: str | None = None
    starting_level: LearningStartingLevel | None = None
    time_budget_minutes_per_week: int | None = None
    target_date: str | None = None
    source_policy: LearningSourcePolicy | None = None


@dataclass(frozen=True, slots=True)
class LearningTaskPatch:
    topic: str | UnsetValue = UNSET
    desired_outcome: str | None | UnsetValue = UNSET
    starting_level: LearningStartingLevel | None | UnsetValue = UNSET
    time_budget_minutes_per_week: int | None | UnsetValue = UNSET
    target_date: str | None | UnsetValue = UNSET
    source_policy: LearningSourcePolicy | None | UnsetValue = UNSET


def normalize_task_create(request: LearningTaskCreate) -> LearningTaskCreate:
    """Validate one draft request without calling a model or external provider."""
    if not isinstance(request, LearningTaskCreate):
        raise TypeError("request must be LearningTaskCreate")
    return LearningTaskCreate(
        topic=_bounded_text(request.topic, "topic", maximum=500),
        desired_outcome=_optional_text(request.desired_outcome, "desired_outcome", maximum=2_000),
        starting_level=_starting_level(request.starting_level),
        time_budget_minutes_per_week=_time_budget(request.time_budget_minutes_per_week),
        target_date=_target_date(request.target_date),
        source_policy=_source_policy(request.source_policy),
    )


def normalize_task_patch(request: LearningTaskPatch) -> LearningTaskPatch:
    """Validate fields supplied for a draft CAS update."""
    if not isinstance(request, LearningTaskPatch):
        raise TypeError("patch must be LearningTaskPatch")
    if all(
        isinstance(value, UnsetValue)
        for value in (
            request.topic,
            request.desired_outcome,
            request.starting_level,
            request.time_budget_minutes_per_week,
            request.target_date,
            request.source_policy,
        )
    ):
        raise ValueError("learning task patch must change at least one field")
    return LearningTaskPatch(
        topic=(
            _bounded_text(request.topic, "topic", maximum=500)
            if not isinstance(request.topic, UnsetValue)
            else UNSET
        ),
        desired_outcome=(
            _optional_text(request.desired_outcome, "desired_outcome", maximum=2_000)
            if not isinstance(request.desired_outcome, UnsetValue)
            else UNSET
        ),
        starting_level=(
            _starting_level(request.starting_level)
            if not isinstance(request.starting_level, UnsetValue)
            else UNSET
        ),
        time_budget_minutes_per_week=(
            _time_budget(request.time_budget_minutes_per_week)
            if not isinstance(request.time_budget_minutes_per_week, UnsetValue)
            else UNSET
        ),
        target_date=(
            _target_date(request.target_date)
            if not isinstance(request.target_date, UnsetValue)
            else UNSET
        ),
        source_policy=(
            _source_policy(request.source_policy)
            if not isinstance(request.source_policy, UnsetValue)
            else UNSET
        ),
    )


def apply_task_patch(task: LearningTask, patch: LearningTaskPatch) -> LearningTaskCreate:
    """Project a partial draft update back into the validated creation shape."""
    normalized = normalize_task_patch(patch)
    return normalize_task_create(
        LearningTaskCreate(
            topic=(
                normalized.topic if not isinstance(normalized.topic, UnsetValue) else task.topic
            ),
            desired_outcome=(
                normalized.desired_outcome
                if not isinstance(normalized.desired_outcome, UnsetValue)
                else task.desired_outcome
            ),
            starting_level=(
                normalized.starting_level
                if not isinstance(normalized.starting_level, UnsetValue)
                else task.learner_profile.starting_level
            ),
            time_budget_minutes_per_week=(
                normalized.time_budget_minutes_per_week
                if not isinstance(normalized.time_budget_minutes_per_week, UnsetValue)
                else task.learner_profile.time_budget_minutes_per_week
            ),
            target_date=(
                normalized.target_date
                if not isinstance(normalized.target_date, UnsetValue)
                else task.learner_profile.target_date
            ),
            source_policy=(
                normalized.source_policy
                if not isinstance(normalized.source_policy, UnsetValue)
                else task.source_policy
            ),
        )
    )


def resolve_source_policy(
    topic: str, requested: LearningSourcePolicy | None
) -> LearningSourcePolicy:
    """Apply explicit no-network language as a hard constraint over defaults."""
    policy = requested or LearningSourcePolicy()
    normalized_topic = "".join(topic.lower().split())
    knowledge_only = any(
        phrase in normalized_topic
        for phrase in ("只用我的知识库", "仅用我的知识库", "只使用我的知识库", "只用知识库")
    )
    no_web = knowledge_only or any(
        phrase in normalized_topic
        for phrase in ("不要联网", "禁止联网", "不允许联网", "不要网页搜索", "不要搜索网页")
    )
    if not no_web:
        return policy
    return LearningSourcePolicy(
        knowledge="required" if knowledge_only else policy.knowledge,
        web="forbidden",
        domains=policy.domains,
        freshness=policy.freshness,
    )


def source_policy_revision(policy: LearningSourcePolicy) -> str:
    """Return the canonical revision for all four frozen source-policy dimensions."""
    normalized = _source_policy(policy)
    if normalized is None:
        raise ValueError("source policy is required")
    payload = {
        "knowledge": normalized.knowledge,
        "web": normalized.web,
        "domains": list(normalized.domains),
        "freshness": normalized.freshness,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "lsrc_" + hashlib.sha256(canonical.encode()).hexdigest()[:32]


def risk_for_topic(topic: str) -> tuple[LearningRiskClass, str | None]:
    """Classify only the conservative financial-education boundary needed by V1."""
    normalized = topic.lower()
    financial = any(
        keyword in normalized
        for keyword in (
            "金融",
            "投资",
            "股票",
            "证券",
            "基金",
            "债券",
            "期货",
            "理财",
            "financial",
            "investing",
            "stock",
        )
    )
    if financial:
        return "financial_education", FINANCIAL_EDUCATION_NOTICE
    return "general_education", None


def clarification_for(request: LearningTaskCreate) -> LearningClarification:
    """Return deterministic missing fields; no model may fill them before confirmation."""
    missing: list[str] = []
    questions: list[LearningClarificationQuestion] = []
    candidates = (
        (
            "desired_outcome",
            request.desired_outcome,
            "完成这次学习后，你希望自己能够独立完成什么？",
        ),
        ("starting_level", request.starting_level, "你目前对这个主题的了解程度是什么？"),
        (
            "time_budget_minutes_per_week",
            request.time_budget_minutes_per_week,
            "你每周大约可以投入多少分钟？",
        ),
    )
    for field, value, prompt in candidates:
        if value is None:
            missing.append(field)
            questions.append(LearningClarificationQuestion(field=field, prompt=prompt))
    return LearningClarification(
        required_fields=tuple(missing),
        questions=tuple(questions),
        ready_to_activate=not missing,
    )


def _source_policy(value: LearningSourcePolicy | None) -> LearningSourcePolicy | None:
    if value is None:
        return None
    if not isinstance(value, LearningSourcePolicy):
        raise TypeError("source_policy must be LearningSourcePolicy")
    if value.knowledge not in {"preferred", "required", "disabled"}:
        raise ValueError("unsupported knowledge source policy")
    if value.web not in {"allowed_when_insufficient", "forbidden"}:
        raise ValueError("unsupported web source policy")
    if value.freshness not in {"all", "current"}:
        raise ValueError("unsupported source freshness")
    domains = tuple(dict.fromkeys(_domain(item) for item in value.domains))
    if len(domains) > 20:
        raise ValueError("source policy domains must contain at most 20 items")
    return LearningSourcePolicy(
        knowledge=value.knowledge,
        web=value.web,
        domains=domains,
        freshness=value.freshness,
    )


def _domain(value: str) -> str:
    domain = value.strip().lower()
    if not domain or len(domain) > 253 or "/" in domain or ":" in domain or " " in domain:
        raise ValueError("source policy domain must be a bare hostname")
    return domain


def _starting_level(value: LearningStartingLevel | None) -> LearningStartingLevel | None:
    if value is not None and value not in {"beginner", "intermediate", "advanced"}:
        raise ValueError("unsupported starting level")
    return value


def _time_budget(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not 15 <= value <= 10_080:
        raise ValueError("time budget must be between 15 and 10080 minutes per week")
    return value


def _target_date(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    try:
        date.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("target_date must use YYYY-MM-DD") from exc
    return normalized


def _optional_text(value: str | None, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    return _bounded_text(value, field, maximum=maximum)


def _bounded_text(value: str, field: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{field} must contain 1 to {maximum} characters")
    return normalized


__all__ = [
    "FINANCIAL_EDUCATION_NOTICE",
    "UNSET",
    "LearningClarification",
    "LearningClarificationQuestion",
    "LearningKnowledgePolicy",
    "LearningLearnerProfile",
    "LearningRiskClass",
    "LearningSourceFreshness",
    "LearningSourcePolicy",
    "LearningStartingLevel",
    "LearningTask",
    "LearningTaskCreate",
    "LearningTaskPatch",
    "LearningTaskStatus",
    "LearningWebPolicy",
    "UnsetValue",
    "apply_task_patch",
    "clarification_for",
    "normalize_task_create",
    "resolve_source_policy",
    "risk_for_topic",
    "source_policy_revision",
]
