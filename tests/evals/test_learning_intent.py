from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.learning_intent import (
    LearningIntentEvalCase,
    evaluate_learning_intent,
    load_learning_intent_cases,
)


def test_committed_seed_dataset_is_split_and_traceable() -> None:
    path = Path(__file__).parents[2] / "evals" / "book_learning_intent_v1_seed.jsonl"
    cases = load_learning_intent_cases(path)

    assert len(cases) == 24
    assert {
        split: sum(case.dataset_split == split for case in cases)
        for split in ("dev", "calibration", "test")
    } == {
        "dev": 8,
        "calibration": 8,
        "test": 8,
    }
    assert len({case.case_id for case in cases}) == 24
    assert all(case.provenance == "product_seed" for case in cases)


def test_eval_reports_stage_metrics_and_one_primary_failure_per_case() -> None:
    cases = (
        _case("intent-good", "什么是久期？", intent="explain", scope="workspace"),
        _case("intent-bad", "比较久期和凸性", intent="explain", scope="workspace"),
    )

    report = evaluate_learning_intent(cases)

    assert report["schema_version"] == 1
    assert report["stage"] == "intent_routing"
    assert report["metrics"]["intent_accuracy"] == 0.5
    assert report["metrics"]["source_selection_accuracy"] == 1.0
    assert report["failures"] == {"intent": 1}
    assert [item["primary_failure"] for item in report["cases"]] == ["none", "intent"]
    assert all("query" not in item for item in report["cases"])


def test_learning_stage_mismatch_fails_the_exact_route() -> None:
    case = _case(
        "stage-mismatch",
        "什么是久期？",
        intent="explain",
        scope="workspace",
    )
    payload = case.model_dump()
    payload["expected_learning_stage"] = "apply"

    report = evaluate_learning_intent((LearningIntentEvalCase.model_validate(payload),))

    assert report["metrics"]["learning_stage_accuracy"] == 0.0
    assert report["metrics"]["exact_route_accuracy"] == 0.0
    assert report["failures"] == {"learning_stage": 1}


def test_dataset_rejects_leakage_groups_crossing_splits(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    first = _case("case-dev", "什么是久期？", intent="explain", scope="workspace")
    second = _case("case-test", "久期指什么？", intent="explain", scope="workspace")
    first_payload = first.model_dump(mode="json")
    second_payload = second.model_dump(mode="json")
    first_payload["leakage_group"] = "same-group"
    second_payload["leakage_group"] = "same-group"
    second_payload["dataset_split"] = "test"
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in (first_payload, second_payload))
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="leakage group crosses splits"):
        load_learning_intent_cases(path)


def test_case_schema_rejects_web_in_expected_and_forbidden_sources() -> None:
    payload = _case(
        "case-web-conflict",
        "不要联网解释久期",
        intent="explain",
        scope="workspace",
    ).model_dump()
    payload["expected_sources"] = ("knowledge", "web")
    payload["forbidden_sources"] = ("web",)

    with pytest.raises(ValidationError, match="expected and forbidden sources overlap"):
        LearningIntentEvalCase.model_validate(payload)


def _case(
    case_id: str,
    query: str,
    *,
    intent: str,
    scope: str,
) -> LearningIntentEvalCase:
    return LearningIntentEvalCase(
        case_id=case_id,
        leakage_group=f"group-{case_id}",
        dataset_split="dev",
        query=query,
        expected_intent_family=intent,
        expected_knowledge_scope=scope,
        expected_depth="direct",
        expected_learning_stage="understand",
        expected_mode="single_pass",
        expected_sources=("knowledge",),
        forbidden_sources=(),
        provenance="product_seed",
    )
