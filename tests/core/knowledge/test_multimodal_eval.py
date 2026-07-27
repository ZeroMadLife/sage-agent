from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from core.knowledge.multimodal_eval import MultimodalEvalCase, load_multimodal_cases

ROOT = Path(__file__).resolve().parents[3]
CASES = ROOT / "knowledge/eval/multimodal_cases.jsonl"
SCHEMA = ROOT / "knowledge/eval/multimodal_case.schema.json"


def test_multimodal_cases_are_versioned_and_split_without_leakage() -> None:
    cases = load_multimodal_cases(CASES)

    assert len(cases) == 12
    assert {case.layer for case in cases} == {"L1", "L2"}
    assert sum(case.gold_bbox is not None for case in cases) == 7
    assert all(
        case.dataset_split == "test" for case in cases if case.case_id.startswith("ragmm-test")
    )
    assert set(json.loads(SCHEMA.read_text())["required"]) == set(MultimodalEvalCase.model_fields)


def test_multimodal_contract_rejects_non_normalized_bbox() -> None:
    payload = load_multimodal_cases(CASES)[0].model_dump()
    payload.update({"gold_page": 1, "gold_bbox": (-1.0, 0.0, 2.0, 1.0)})

    with pytest.raises(ValueError, match="normalized"):
        MultimodalEvalCase.model_validate(payload)


def test_multimodal_report_is_reproducible_and_marks_fixture_scope(tmp_path: Path) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = f"{ROOT / 'packages/sage_harness'}:{ROOT}"
    command = [
        sys.executable,
        str(ROOT / "scripts/evaluate_knowledge_multimodal.py"),
        "--allow-dirty",
    ]
    for output in (first, second):
        subprocess.run(
            [*command, "--output", str(output)],
            cwd=ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )

    first_report = json.loads(first.read_text())
    second_report = json.loads(second.read_text())
    assert first_report["deterministic_digest"] == second_report["deterministic_digest"]
    assert first_report["gates"]["overall_passed"] is True
    assert first_report["scope"]["live_vlm_quality_evaluated"] is False
    assert first_report["scope"]["visual_vector_retrieval_enabled"] is False
