"""Git-backed learning-goal contract for the local Knowledge workspace."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

_GOAL_SCHEMA_VERSION = 1
_GOAL_FENCE = re.compile(r"```sage-learning-goal\s*\n(?P<payload>\{.*?\})\s*\n```", re.DOTALL)
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_MAX_PURPOSE_BYTES = 256 * 1024


class LearningGoalError(ValueError):
    """The purpose document does not contain a valid learning-goal contract."""


@dataclass(frozen=True, slots=True)
class LearningCapability:
    capability_id: str
    label: str
    description: str
    keywords: tuple[str, ...]
    weight: float
    required: bool


@dataclass(frozen=True, slots=True)
class LearningGoalDefinition:
    goal_id: str
    title: str
    description: str
    capabilities: tuple[LearningCapability, ...]


@dataclass(frozen=True, slots=True)
class LearningGoal:
    schema_version: int
    goal_id: str
    title: str
    description: str
    capabilities: tuple[LearningCapability, ...]
    goal_revision: str
    git_commit: str
    structured: bool

    def definition(self) -> LearningGoalDefinition:
        return LearningGoalDefinition(
            goal_id=self.goal_id,
            title=self.title,
            description=self.description,
            capabilities=self.capabilities,
        )


def parse_learning_goal(content: str, *, git_commit: str) -> LearningGoal:
    if len(content.encode("utf-8")) > _MAX_PURPOSE_BYTES:
        raise LearningGoalError("purpose document is too large")
    match = _GOAL_FENCE.search(content)
    revision = "kgoal_" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]
    if match is None:
        description = _legacy_description(content)
        return LearningGoal(
            schema_version=_GOAL_SCHEMA_VERSION,
            goal_id="personal-learning",
            title="个人知识与能力持续成长",
            description=description,
            capabilities=(),
            goal_revision=revision,
            git_commit=git_commit,
            structured=False,
        )
    try:
        payload = json.loads(match.group("payload"))
    except json.JSONDecodeError as exc:
        raise LearningGoalError("learning goal JSON is invalid") from exc
    definition = validate_learning_goal(payload)
    schema_version = payload.get("schema_version")
    if schema_version != _GOAL_SCHEMA_VERSION:
        raise LearningGoalError("unsupported learning goal schema version")
    return LearningGoal(
        schema_version=_GOAL_SCHEMA_VERSION,
        goal_id=definition.goal_id,
        title=definition.title,
        description=definition.description,
        capabilities=definition.capabilities,
        goal_revision=revision,
        git_commit=git_commit,
        structured=True,
    )


def validate_learning_goal(payload: object) -> LearningGoalDefinition:
    if not isinstance(payload, dict):
        raise LearningGoalError("learning goal must be an object")
    goal_id = _identifier(payload.get("goal_id"), "goal_id")
    title = _bounded_text(payload.get("title"), "title", 1, 160)
    description = _bounded_text(payload.get("description", ""), "description", 0, 2_000)
    raw_capabilities = payload.get("capabilities")
    if not isinstance(raw_capabilities, list) or len(raw_capabilities) > 32:
        raise LearningGoalError("capabilities must contain at most 32 items")
    capabilities: list[LearningCapability] = []
    seen: set[str] = set()
    for raw in raw_capabilities:
        if not isinstance(raw, dict):
            raise LearningGoalError("capability must be an object")
        capability_id = _identifier(raw.get("capability_id"), "capability_id")
        if capability_id in seen:
            raise LearningGoalError("capability identifiers must be unique")
        seen.add(capability_id)
        label = _bounded_text(raw.get("label"), "capability label", 1, 120)
        capability_description = _bounded_text(
            raw.get("description", ""), "capability description", 0, 1_000
        )
        raw_keywords = raw.get("keywords")
        if not isinstance(raw_keywords, list) or not 1 <= len(raw_keywords) <= 24:
            raise LearningGoalError("capability keywords must contain 1 to 24 items")
        keywords = tuple(
            dict.fromkeys(
                _bounded_text(keyword, "capability keyword", 1, 80) for keyword in raw_keywords
            )
        )
        raw_weight = raw.get("weight", 1.0)
        if isinstance(raw_weight, bool) or not isinstance(raw_weight, int | float):
            raise LearningGoalError("capability weight must be numeric")
        weight = float(raw_weight)
        if not 0.1 <= weight <= 10.0:
            raise LearningGoalError("capability weight must be between 0.1 and 10")
        required = raw.get("required", True)
        if not isinstance(required, bool):
            raise LearningGoalError("capability required must be boolean")
        capabilities.append(
            LearningCapability(
                capability_id=capability_id,
                label=label,
                description=capability_description,
                keywords=keywords,
                weight=weight,
                required=required,
            )
        )
    return LearningGoalDefinition(
        goal_id=goal_id,
        title=title,
        description=description,
        capabilities=tuple(capabilities),
    )


def render_learning_goal(definition: LearningGoalDefinition) -> str:
    validated = validate_learning_goal(
        {
            "goal_id": definition.goal_id,
            "title": definition.title,
            "description": definition.description,
            "capabilities": [
                {
                    "capability_id": item.capability_id,
                    "label": item.label,
                    "description": item.description,
                    "keywords": list(item.keywords),
                    "weight": item.weight,
                    "required": item.required,
                }
                for item in definition.capabilities
            ],
        }
    )
    payload = {
        "schema_version": _GOAL_SCHEMA_VERSION,
        "goal_id": validated.goal_id,
        "title": validated.title,
        "description": validated.description,
        "capabilities": [
            {
                "capability_id": item.capability_id,
                "label": item.label,
                "description": item.description,
                "keywords": list(item.keywords),
                "weight": item.weight,
                "required": item.required,
            }
            for item in validated.capabilities
        ],
    }
    document = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        "# Purpose\n\n"
        f"## {validated.title}\n\n"
        f"{validated.description}\n\n"
        "```sage-learning-goal\n"
        f"{document}\n"
        "```\n"
    )


def default_learning_goal_content() -> str:
    return render_learning_goal(
        LearningGoalDefinition(
            goal_id="personal-learning",
            title="个人知识与能力持续成长",
            description="通过可追溯资料、项目实践和阶段复盘持续构建个人知识库。",
            capabilities=(),
        )
    )


def _legacy_description(content: str) -> str:
    lines = [line.strip() for line in content.splitlines()]
    paragraphs = [line for line in lines if line and not line.startswith("#")]
    description = " ".join(paragraphs)[:2_000]
    return description or "通过可追溯资料和项目实践持续构建个人知识库。"


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise LearningGoalError(f"{field} must be a lowercase identifier")
    return value


def _bounded_text(value: object, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise LearningGoalError(f"{field} must be text")
    normalized = " ".join(value.split())
    if not minimum <= len(normalized) <= maximum:
        raise LearningGoalError(f"{field} length is invalid")
    return normalized
