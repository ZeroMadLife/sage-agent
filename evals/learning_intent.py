"""Seed-stage evaluation for the production learning intent and retrieval gate."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from core.harness.retrieval_gate import decide_retrieval_gate

_Source = Literal["semantic_memory", "episodic_memory", "knowledge", "web"]
_FAILURE_ORDER = (
    "constraint",
    "intent",
    "scope",
    "depth",
    "learning_stage",
    "agentic",
    "source",
)


class LearningIntentEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    case_id: str
    leakage_group: str
    dataset_split: Literal["dev", "calibration", "test"]
    query: str
    expected_intent_family: Literal[
        "explain", "compare", "plan", "practice", "recall", "research", "meta"
    ]
    expected_knowledge_scope: Literal["none", "workspace", "book", "web", "mixed"]
    expected_depth: Literal["direct", "multi_hop"]
    expected_learning_stage: Literal["discover", "understand", "apply", "assess"]
    expected_mode: Literal["skip", "single_pass", "agentic_candidate"]
    expected_sources: tuple[_Source, ...]
    forbidden_sources: tuple[_Source, ...]
    provenance: Literal["product_seed"]

    @field_validator("case_id", "leakage_group")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if (
            len(value) < 3
            or len(value) > 80
            or not all(char.islower() or char.isdigit() or char in "._-" for char in value)
        ):
            raise ValueError("learning intent eval ids are invalid")
        return value

    @field_validator("query")
    @classmethod
    def _validate_query(cls, value: str) -> str:
        if len(value) < 3 or len(value) > 1_000:
            raise ValueError("learning intent eval query is invalid")
        return value

    @field_validator("expected_sources", "forbidden_sources")
    @classmethod
    def _validate_sources(cls, value: tuple[_Source, ...]) -> tuple[_Source, ...]:
        if len(value) != len(set(value)):
            raise ValueError("learning intent eval sources must be unique")
        return value

    @model_validator(mode="after")
    def _validate_source_policy(self) -> LearningIntentEvalCase:
        if set(self.expected_sources).intersection(self.forbidden_sources):
            raise ValueError("expected and forbidden sources overlap")
        return self


def load_learning_intent_cases(path: Path) -> tuple[LearningIntentEvalCase, ...]:
    cases: list[LearningIntentEvalCase] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(LearningIntentEvalCase.model_validate_json(line))
        except ValueError as exc:
            raise ValueError(f"invalid learning intent case at line {line_number}") from exc
    if not cases:
        raise ValueError("learning intent eval cases must not be empty")
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("learning intent case ids must be unique")
    queries = [" ".join(case.query.casefold().split()) for case in cases]
    if len(queries) != len(set(queries)):
        raise ValueError("learning intent queries must be unique")
    group_splits: dict[str, set[str]] = {}
    for case in cases:
        group_splits.setdefault(case.leakage_group, set()).add(case.dataset_split)
    if any(len(splits) > 1 for splits in group_splits.values()):
        raise ValueError("learning intent leakage group crosses splits")
    return tuple(cases)


def evaluate_learning_intent(
    cases: tuple[LearningIntentEvalCase, ...],
) -> dict[str, Any]:
    if not cases:
        raise ValueError("learning intent eval cases must not be empty")
    evaluated = [_evaluate_case(case) for case in cases]
    return {
        "schema_version": 1,
        "stage": "intent_routing",
        "protocol": {
            "dataset_provenance": "product_seed",
            "formal_model_accuracy_claim": False,
            "test_used_for_tuning": False,
            "production_seam": "core.harness.retrieval_gate.decide_retrieval_gate",
        },
        "metrics": _metrics(evaluated),
        "splits": {
            split: _metrics([item for item in evaluated if item["dataset_split"] == split])
            for split in ("dev", "calibration", "test")
        },
        "failures": dict(
            sorted(
                Counter(
                    item["primary_failure"]
                    for item in evaluated
                    if item["primary_failure"] != "none"
                ).items()
            )
        ),
        "cases": evaluated,
    }


def _evaluate_case(case: LearningIntentEvalCase) -> dict[str, Any]:
    receipt = decide_retrieval_gate(
        case.query,
        memory_available=True,
        knowledge_available=True,
        web_available=True,
    )
    route = receipt.intent_route
    actual_sources = frozenset(receipt.selected_sources)
    expected_sources = frozenset(case.expected_sources)
    constraint_ok = not actual_sources.intersection(case.forbidden_sources)
    checks = {
        "constraint": constraint_ok,
        "intent": route.intent_family == case.expected_intent_family,
        "scope": route.knowledge_scope == case.expected_knowledge_scope,
        "depth": route.depth == case.expected_depth,
        "learning_stage": route.learning_stage == case.expected_learning_stage,
        "agentic": route.recommended_mode == case.expected_mode,
        "source": actual_sources == expected_sources,
    }
    primary_failure = next((name for name in _FAILURE_ORDER if not checks[name]), "none")
    return {
        "case_id": case.case_id,
        "dataset_split": case.dataset_split,
        "primary_failure": primary_failure,
        "checks": checks,
        "expected": {
            "intent_family": case.expected_intent_family,
            "knowledge_scope": case.expected_knowledge_scope,
            "depth": case.expected_depth,
            "learning_stage": case.expected_learning_stage,
            "recommended_mode": case.expected_mode,
            "selected_sources": sorted(expected_sources),
        },
        "actual": {
            "intent_family": route.intent_family,
            "knowledge_scope": route.knowledge_scope,
            "depth": route.depth,
            "learning_stage": route.learning_stage,
            "recommended_mode": route.recommended_mode,
            "selected_sources": sorted(actual_sources),
            "routing_confidence": route.routing_confidence,
            "degraded": receipt.degraded,
        },
    }


def _metrics(cases: list[dict[str, Any]]) -> dict[str, float | int]:
    if not cases:
        return {
            "case_count": 0,
            "intent_accuracy": 0.0,
            "intent_macro_f1": 0.0,
            "scope_accuracy": 0.0,
            "scope_macro_f1": 0.0,
            "depth_accuracy": 0.0,
            "learning_stage_accuracy": 0.0,
            "agentic_mode_accuracy": 0.0,
            "agentic_precision": 0.0,
            "agentic_recall": 0.0,
            "agentic_f1": 0.0,
            "source_selection_accuracy": 0.0,
            "constraint_adherence": 0.0,
            "exact_route_accuracy": 0.0,
        }
    expected_intents = [str(item["expected"]["intent_family"]) for item in cases]
    actual_intents = [str(item["actual"]["intent_family"]) for item in cases]
    expected_scopes = [str(item["expected"]["knowledge_scope"]) for item in cases]
    actual_scopes = [str(item["actual"]["knowledge_scope"]) for item in cases]
    expected_agentic = [
        item["expected"]["recommended_mode"] == "agentic_candidate" for item in cases
    ]
    actual_agentic = [item["actual"]["recommended_mode"] == "agentic_candidate" for item in cases]
    true_positive = sum(
        expected and actual
        for expected, actual in zip(expected_agentic, actual_agentic, strict=True)
    )
    false_positive = sum(
        not expected and actual
        for expected, actual in zip(expected_agentic, actual_agentic, strict=True)
    )
    false_negative = sum(
        expected and not actual
        for expected, actual in zip(expected_agentic, actual_agentic, strict=True)
    )
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    return {
        "case_count": len(cases),
        "intent_accuracy": _check_rate(cases, "intent"),
        "intent_macro_f1": _macro_f1(expected_intents, actual_intents),
        "scope_accuracy": _check_rate(cases, "scope"),
        "scope_macro_f1": _macro_f1(expected_scopes, actual_scopes),
        "depth_accuracy": _check_rate(cases, "depth"),
        "learning_stage_accuracy": _accuracy(
            [str(item["expected"]["learning_stage"]) for item in cases],
            [str(item["actual"]["learning_stage"]) for item in cases],
        ),
        "agentic_mode_accuracy": _check_rate(cases, "agentic"),
        "agentic_precision": precision,
        "agentic_recall": recall,
        "agentic_f1": _f1(precision, recall),
        "source_selection_accuracy": _check_rate(cases, "source"),
        "constraint_adherence": _check_rate(cases, "constraint"),
        "exact_route_accuracy": _ratio(
            sum(all(item["checks"].values()) for item in cases), len(cases)
        ),
    }


def _check_rate(cases: list[dict[str, Any]], key: str) -> float:
    return _ratio(sum(bool(item["checks"][key]) for item in cases), len(cases))


def _accuracy(expected: list[str], actual: list[str]) -> float:
    return _ratio(
        sum(left == right for left, right in zip(expected, actual, strict=True)),
        len(expected),
    )


def _macro_f1(expected: list[str], actual: list[str]) -> float:
    labels = sorted(set(expected).union(actual))
    if not labels:
        return 0.0
    scores: list[float] = []
    for label in labels:
        true_positive = sum(
            left == label and right == label for left, right in zip(expected, actual, strict=True)
        )
        false_positive = sum(
            left != label and right == label for left, right in zip(expected, actual, strict=True)
        )
        false_negative = sum(
            left == label and right != label for left, right in zip(expected, actual, strict=True)
        )
        scores.append(
            _f1(
                _ratio(true_positive, true_positive + false_positive),
                _ratio(true_positive, true_positive + false_negative),
            )
        )
    return round(sum(scores) / len(scores), 4)


def _f1(precision: float, recall: float) -> float:
    return round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


__all__ = [
    "LearningIntentEvalCase",
    "evaluate_learning_intent",
    "load_learning_intent_cases",
]
