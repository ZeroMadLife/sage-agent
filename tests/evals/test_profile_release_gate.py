"""Release readiness checks for switching new sessions to DeerFlow V2."""

from __future__ import annotations

from dataclasses import replace

import pytest

from evals.coding.profile_parity import (
    ProfileParityReport,
    ProfileScenarioResult,
    RuntimeProfile,
)
from evals.coding.release_gate import (
    ReleaseGateEvidence,
    evaluate_release_gate,
)


def _result(
    scenario: str,
    profile: RuntimeProfile,
    *,
    first_token_ms: float = 100.0,
    duration_ms: float = 200.0,
    assertions_passed: bool = True,
    streamed: bool = True,
    policy_compliant: bool = True,
    tool_errors: int = 0,
) -> ProfileScenarioResult:
    return ProfileScenarioResult(
        scenario=scenario,
        runtime_profile=profile,
        completed=True,
        final_seen=True,
        assertions_passed=assertions_passed,
        streamed=streamed,
        tool_calls=1,
        tool_call_events=1,
        tool_results=1,
        paired_tool_events=1,
        tool_errors=tool_errors,
        unpaired_tool_calls=0,
        unpaired_tool_results=0,
        policy_compliant=policy_compliant,
        first_token_ms=first_token_ms,
        duration_ms=duration_ms,
    )


def _passing_evidence() -> ReleaseGateEvidence:
    report = ProfileParityReport(
        results=[
            _result("stream", "legacy"),
            _result(
                "stream",
                "deerflow_v2",
                first_token_ms=105.0,
                duration_ms=210.0,
            ),
            _result("tool", "legacy", first_token_ms=120.0, duration_ms=240.0),
            _result(
                "tool",
                "deerflow_v2",
                first_token_ms=125.0,
                duration_ms=250.0,
            ),
        ]
    )
    return ReleaseGateEvidence(
        report=report,
        required_scenarios=("stream", "tool"),
        real_provider_sampled=True,
        timeline_replay_passed=True,
        browser_streaming_passed=True,
        browser_tool_passed=True,
        browser_replay_passed=True,
        browser_refresh_passed=True,
        browser_approval_resume_passed=True,
        container_sandbox_passed=True,
        server_container_sandbox_configured=True,
        rollback_available=True,
    )


def test_release_gate_allows_only_complete_evidence() -> None:
    decision = evaluate_release_gate(_passing_evidence())

    assert decision.ready is True
    assert decision.blockers == ()
    assert decision.paired_scenarios == ("stream", "tool")
    assert decision.to_dict()["ready"] is True


def test_release_gate_rejects_missing_or_failed_scenarios() -> None:
    evidence = _passing_evidence()
    report = ProfileParityReport(
        results=[
            result
            for result in evidence.report.results
            if not (result.scenario == "tool" and result.runtime_profile == "deerflow_v2")
        ]
    )
    report.results[1] = replace(report.results[1], assertions_passed=False)

    decision = evaluate_release_gate(replace(evidence, report=report))

    assert decision.ready is False
    assert "scenario_failed:deerflow_v2:stream" in decision.blockers
    assert "scenario_missing:deerflow_v2:tool" in decision.blockers
    assert "scenario_regression:stream" in decision.blockers


def test_release_gate_rejects_metric_and_latency_regressions() -> None:
    evidence = _passing_evidence()
    results = list(evidence.report.results)
    results[1] = replace(
        results[1],
        streamed=False,
        policy_compliant=False,
        tool_errors=1,
        first_token_ms=200.0,
        duration_ms=400.0,
    )

    decision = evaluate_release_gate(replace(evidence, report=ProfileParityReport(results=results)))

    assert decision.ready is False
    assert "deerflow_v2_task_completion_below_legacy" in decision.blockers
    assert "deerflow_v2_tool_success_below_legacy" in decision.blockers
    assert "deerflow_v2_policy_compliance_below_100_percent" in decision.blockers
    assert "deerflow_v2_streaming_below_100_percent" in decision.blockers
    assert "deerflow_v2_p95_first_token_above_legacy" in decision.blockers
    assert "deerflow_v2_p95_duration_above_legacy" in decision.blockers


def test_release_gate_rejects_missing_latency_samples() -> None:
    evidence = _passing_evidence()
    results = [
        replace(result, first_token_ms=None, duration_ms=0.0) for result in evidence.report.results
    ]

    decision = evaluate_release_gate(replace(evidence, report=ProfileParityReport(results=results)))

    assert "p95_first_token_missing" in decision.blockers
    assert "p95_duration_missing" in decision.blockers


@pytest.mark.parametrize(
    ("field", "blocker"),
    [
        ("real_provider_sampled", "real_provider_not_sampled"),
        ("timeline_replay_passed", "timeline_replay_failed"),
        ("browser_streaming_passed", "browser_streaming_failed"),
        ("browser_tool_passed", "browser_tool_failed"),
        ("browser_replay_passed", "browser_replay_failed"),
        ("browser_refresh_passed", "browser_refresh_failed"),
        ("browser_approval_resume_passed", "browser_approval_resume_failed"),
        ("container_sandbox_passed", "container_sandbox_smoke_failed"),
        (
            "server_container_sandbox_configured",
            "server_container_sandbox_not_configured",
        ),
        ("rollback_available", "session_profile_rollback_unavailable"),
    ],
)
def test_release_gate_fails_closed_when_external_evidence_is_missing(
    field: str,
    blocker: str,
) -> None:
    decision = evaluate_release_gate(replace(_passing_evidence(), **{field: False}))

    assert decision.ready is False
    assert blocker in decision.blockers


def test_release_gate_validates_its_thresholds_and_scenario_set() -> None:
    evidence = _passing_evidence()

    with pytest.raises(ValueError, match="at least 1.0"):
        replace(evidence, max_latency_ratio=0.99)
    with pytest.raises(ValueError, match="must be unique"):
        replace(evidence, required_scenarios=("stream", "stream"))
