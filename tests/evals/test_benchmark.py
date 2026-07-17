"""Benchmark tests for the Sage coding harness."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from evals.coding.metrics import BenchmarkReport, ScenarioResult
from evals.coding.runner import _write_reports, run_benchmark, run_scenario
from evals.coding.scenarios import SCENARIOS


async def test_benchmark_scenarios_run() -> None:
    """All 10 scenarios run to completion via the real CodingRuntime.

    Driving each scenario through ``CodingRuntime.run_turn`` exercises the
    active-run lease, run_finished terminal event, workspace diff artifact,
    and memory injection. This is a smoke test: it does not assert pass/fail
    per scenario, only that every scenario produces a ScenarioResult. Failure
    details surface in the result.detail field.
    """
    results = [await run_scenario(scenario) for scenario in SCENARIOS]

    assert len(results) == 10
    names = {r.name for r in results}
    assert names == {scenario.name for scenario in SCENARIOS}
    # Every result has a populated category and non-negative counts.
    for result in results:
        assert result.category in {
            "read_explain",
            "controlled_edit",
            "policy_boundary",
            "memory_continuity",
        }
        assert result.tool_calls >= 0
        assert result.tool_errors >= 0
        assert result.duration_ms >= 0
        assert isinstance(result.detail, str)


async def test_controlled_edit_scenarios_emit_run_finished_and_diff() -> None:
    """Controlled-edit scenarios pass and drive the full runtime lifecycle.

    These scenarios run through the real runtime, which emits
    ``run_finished`` and ``workspace_diff_ready`` terminal events. A passing
    result means those lifecycle assertions held (the runner flips
    ``passed`` to False with a specific detail otherwise).
    """
    edit_scenarios = [s for s in SCENARIOS if s.category == "controlled_edit"]
    assert len(edit_scenarios) == 3
    for scenario in edit_scenarios:
        result = await run_scenario(scenario)
        assert result.passed, f"{scenario.name} failed: {result.detail}"


async def test_memory_continuity_recalls_in_second_session() -> None:
    """Memory scenarios persist a fact and recall it in a fresh runtime.

    The runner spins up a second CodingRuntime on the same workspace and
    checks the durable fact surfaces in the memory context block. A passing
    result means the round-trip (write -> recall) held.
    """
    memory_scenarios = [s for s in SCENARIOS if s.category == "memory_continuity"]
    assert len(memory_scenarios) == 2
    for scenario in memory_scenarios:
        result = await run_scenario(scenario)
        assert result.passed, f"{scenario.name} failed: {result.detail}"


async def test_run_benchmark_returns_full_report() -> None:
    """The full benchmark run covers all scenarios and aggregates metrics."""
    report = await run_benchmark()

    assert len(report.results) == 10
    metrics = report.to_dict()["metrics"]
    # All four headline metrics are present and in valid ranges.
    assert 0.0 <= metrics["task_completion_rate"] <= 1.0
    assert 0.0 <= metrics["tool_call_success_rate"] <= 1.0
    assert 0.0 <= metrics["policy_compliance_rate"] <= 1.0
    assert metrics["p95_turn_latency_ms"] >= 0


def test_metrics_calculation() -> None:
    """BenchmarkReport metric math is correct for a known input."""
    report = BenchmarkReport(
        results=[
            ScenarioResult(
                name="a",
                category="read_explain",
                passed=True,
                tool_calls=3,
                tool_errors=0,
                policy_compliant=True,
                duration_ms=100,
            ),
            ScenarioResult(
                name="b",
                category="controlled_edit",
                passed=False,
                tool_calls=2,
                tool_errors=1,
                policy_compliant=True,
                duration_ms=200,
            ),
            ScenarioResult(
                name="c",
                category="policy_boundary",
                passed=True,
                tool_calls=1,
                tool_errors=0,
                policy_compliant=False,
                duration_ms=300,
            ),
            ScenarioResult(
                name="d",
                category="memory_continuity",
                passed=True,
                tool_calls=2,
                tool_errors=0,
                policy_compliant=True,
                duration_ms=400,
            ),
        ]
    )

    # 3 of 4 passed.
    assert report.task_completion_rate == 0.75
    # 8 total tool calls, 1 error -> 7/8.
    assert report.tool_call_success_rate == 0.875
    # 3 of 4 policy-compliant.
    assert report.policy_compliance_rate == 0.75
    # latencies sorted: [100, 200, 300, 400]; idx = int(4*0.95)=3 -> 400.
    assert report.p95_turn_latency_ms == 400


def test_metrics_empty_report() -> None:
    """An empty report degrades to safe defaults rather than raising."""
    report = BenchmarkReport()
    assert report.task_completion_rate == 0.0
    # No tool calls means success rate is vacuously 1.0.
    assert report.tool_call_success_rate == 1.0
    assert report.policy_compliance_rate == 1.0
    assert report.p95_turn_latency_ms == 0.0
    d = report.to_dict()
    assert d["metrics"]["task_completion_rate"] == 0.0
    assert d["results"] == []


def test_metrics_single_result_latency() -> None:
    """p95 over one result returns that result's latency."""
    report = BenchmarkReport(
        results=[
            ScenarioResult(
                name="solo",
                category="read_explain",
                passed=True,
                duration_ms=123,
            )
        ]
    )
    assert report.p95_turn_latency_ms == 123


def test_benchmark_is_informational() -> None:
    """The benchmark runner never raises on assertion failures (smoke guard).

    Mirrors the contract stated in the harness design: the benchmark reports
    pass/fail per scenario but does not gate the build. We simulate a failing
    assertion path by running the suite; the test passes as long as no exception
    escapes.
    """
    report = asyncio.run(run_benchmark())
    assert len(report.results) == 10
    # at least one result must carry a category, regardless of pass state.
    assert all(r.category for r in report.results)


def test_html_report_generated(tmp_path: Path) -> None:
    """_write_reports emits a self-contained HTML report file.

    The HTML must be written, non-empty, contain the report title and scenario
    rows, and not reference any external resources.
    """
    report = BenchmarkReport(
        results=[
            ScenarioResult(
                name="read-readme",
                category="read_explain",
                passed=True,
                tool_calls=1,
                tool_errors=0,
                policy_compliant=True,
                duration_ms=42,
                detail="",
            ),
            ScenarioResult(
                name="fix-typo",
                category="controlled_edit",
                passed=False,
                tool_calls=2,
                tool_errors=1,
                policy_compliant=True,
                duration_ms=99,
                detail="files don't match expected",
            ),
        ]
    )

    # _write_reports writes into evals/coding/results relative to cwd; point it
    # there by running from the repo root (tests run with the repo as cwd).
    json_path, md_path, html_path = _write_reports(report)

    assert html_path.suffix == ".html"
    assert html_path.is_file()
    html_text = html_path.read_text(encoding="utf-8")
    assert html_text.startswith("<!DOCTYPE html>")
    assert "Sage V6 Benchmark Report" in html_text
    # Both scenarios appear in the detail table.
    assert "read-readme" in html_text
    assert "fix-typo" in html_text
    # Self-contained: no external stylesheet or script links.
    assert "href=" not in html_text
    assert "<link" not in html_text
    assert "<script" not in html_text
    # Metric cards render the headline rates.
    assert "Task Completion Rate" in html_text
    # The failing detail string is present (HTML-escaped by html.escape, so the
    # apostrophe becomes a numeric entity).
    assert "files don&#x27;t match expected" in html_text
    # The JSON report is still produced alongside the HTML.
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(data["results"]) == 2
    assert md_path.is_file()

    # Cleanup the generated artifacts so the test is hermetic.
    for p in (json_path, md_path, html_path):
        p.unlink(missing_ok=True)


def test_assert_run_finished_and_diff_ready() -> None:
    """The new lifecycle assertions detect the runtime terminal events."""
    from evals.coding.assertions import assert_diff_ready, assert_run_finished

    events_with = [
        {"type": "tool_call", "tool": "read_file"},
        {"type": "final", "content": "done"},
        {"type": "workspace_diff_ready", "file_count": 1},
        {"type": "run_finished", "status": "completed"},
    ]
    assert assert_run_finished(events_with) is True
    assert assert_diff_ready(events_with) is True

    events_without = [
        {"type": "tool_call", "tool": "read_file"},
        {"type": "final", "content": "done"},
    ]
    assert assert_run_finished(events_without) is False
    assert assert_diff_ready(events_without) is False
