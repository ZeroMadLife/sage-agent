from __future__ import annotations

import asyncio
from pathlib import Path

from sage_harness import HarnessConfig

from evals.context_budget import load_manifest, run_evaluation, write_report


def test_context_budget_manifest_is_versioned_and_nontrivial() -> None:
    manifest = load_manifest()

    assert manifest["evaluation_id"] == "sage-context-budget-v2"
    assert len(manifest["cases"]) == 12
    assert len(manifest["threshold_candidates"]) == 4


def test_context_budget_ablation_preserves_invariants_and_reduces_tokens() -> None:
    report = asyncio.run(run_evaluation())
    baseline = report["ablation"]["A0_baseline"]
    budget = report["ablation"]["A1_budget"]
    compact = report["ablation"]["A2_compact"]
    full = report["ablation"]["A3_full"]

    assert report["baseline_diagnosis"]["compact_reachable_before_run_cap"] is False
    assert (
        report["pre_optimization_ablation"]["A2_compact"]["total_model_input_tokens"]
        == report["pre_optimization_ablation"]["A0_baseline"]["total_model_input_tokens"]
    )
    assert budget["total_model_input_tokens"] == baseline["total_model_input_tokens"]
    assert compact["total_model_input_tokens"] < baseline["total_model_input_tokens"]
    assert compact["token_reduction_vs_a0"] > 0
    assert full["exact_user_retention_rate"] == 1.0
    assert full["surviving_tool_pair_valid_rate"] == 1.0
    assert full["decision_marker_retention_rate"] == 1.0
    assert full["artifact_probe_recovery_rate"] == 1.0
    assert report["recommended_threshold"]["working_set_tokens"] in {
        32_000,
        48_000,
        64_000,
        80_000,
    }
    assert report["recommended_threshold"]["working_set_tokens"] == (
        HarnessConfig().context_working_set_tokens
    )
    assert report["recommended_threshold"]["keep_tokens"] == (HarnessConfig().context_keep_tokens)


def test_context_budget_report_writes_json_and_markdown(tmp_path: Path) -> None:
    report = asyncio.run(run_evaluation())
    output = tmp_path / "report.json"

    write_report(report, output)

    assert output.is_file()
    markdown = output.with_suffix(".md")
    assert markdown.is_file()
    assert "A0_baseline" in markdown.read_text(encoding="utf-8")
