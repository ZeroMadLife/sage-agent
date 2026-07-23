"""Fail-closed release readiness gate for the DeerFlow V2 runtime."""

from __future__ import annotations

from dataclasses import dataclass

from evals.coding.profile_parity import ProfileMetrics, ProfileParityReport


@dataclass(frozen=True, slots=True)
class ReleaseGateEvidence:
    """Evidence that cannot be inferred safely from deterministic parity tests."""

    report: ProfileParityReport
    required_scenarios: tuple[str, ...]
    real_provider_sampled: bool
    timeline_replay_passed: bool
    browser_streaming_passed: bool
    browser_tool_passed: bool
    browser_replay_passed: bool
    browser_refresh_passed: bool
    browser_approval_resume_passed: bool
    container_sandbox_passed: bool
    server_container_sandbox_configured: bool
    rollback_available: bool
    max_latency_ratio: float = 1.10

    def __post_init__(self) -> None:
        if self.max_latency_ratio < 1.0:
            raise ValueError("max_latency_ratio must be at least 1.0")
        if len(self.required_scenarios) != len(set(self.required_scenarios)):
            raise ValueError("required_scenarios must be unique")


@dataclass(frozen=True, slots=True)
class ReleaseGateDecision:
    """Machine-readable decision used before changing the default runtime."""

    ready: bool
    blockers: tuple[str, ...]
    paired_scenarios: tuple[str, ...]
    legacy_metrics: ProfileMetrics
    deerflow_v2_metrics: ProfileMetrics
    max_latency_ratio: float

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "blockers": list(self.blockers),
            "paired_scenarios": list(self.paired_scenarios),
            "max_latency_ratio": self.max_latency_ratio,
            "profiles": {
                "legacy": self.legacy_metrics.to_dict(),
                "deerflow_v2": self.deerflow_v2_metrics.to_dict(),
            },
        }


def evaluate_release_gate(evidence: ReleaseGateEvidence) -> ReleaseGateDecision:
    """Require parity, live UX, isolation, and rollback evidence simultaneously."""
    report = evidence.report
    legacy_metrics = report.metrics("legacy")
    v2_metrics = report.metrics("deerflow_v2")
    blockers: list[str] = []
    by_key = {(result.scenario, result.runtime_profile): result for result in report.results}

    if not evidence.required_scenarios:
        blockers.append("required_scenarios_empty")
    for scenario in evidence.required_scenarios:
        for profile in ("legacy", "deerflow_v2"):
            result = by_key.get((scenario, profile))
            if result is None:
                blockers.append(f"scenario_missing:{profile}:{scenario}")
            elif not result.passed:
                blockers.append(f"scenario_failed:{profile}:{scenario}")

    blockers.extend(f"scenario_regression:{scenario}" for scenario in report.regressions())
    if v2_metrics.task_completion_rate < legacy_metrics.task_completion_rate:
        blockers.append("deerflow_v2_task_completion_below_legacy")
    if v2_metrics.tool_call_success_rate < legacy_metrics.tool_call_success_rate:
        blockers.append("deerflow_v2_tool_success_below_legacy")
    if v2_metrics.tool_event_pairing_rate < 1.0:
        blockers.append("deerflow_v2_tool_event_pairing_below_100_percent")
    if v2_metrics.policy_compliance_rate < 1.0:
        blockers.append("deerflow_v2_policy_compliance_below_100_percent")
    if v2_metrics.streaming_rate < 1.0:
        blockers.append("deerflow_v2_streaming_below_100_percent")

    _append_latency_blocker(
        blockers,
        name="first_token",
        legacy=legacy_metrics.p95_first_token_ms,
        deerflow_v2=v2_metrics.p95_first_token_ms,
        max_ratio=evidence.max_latency_ratio,
    )
    _append_latency_blocker(
        blockers,
        name="duration",
        legacy=legacy_metrics.p95_duration_ms,
        deerflow_v2=v2_metrics.p95_duration_ms,
        max_ratio=evidence.max_latency_ratio,
    )

    external_checks = {
        "real_provider_not_sampled": evidence.real_provider_sampled,
        "timeline_replay_failed": evidence.timeline_replay_passed,
        "browser_streaming_failed": evidence.browser_streaming_passed,
        "browser_tool_failed": evidence.browser_tool_passed,
        "browser_replay_failed": evidence.browser_replay_passed,
        "browser_refresh_failed": evidence.browser_refresh_passed,
        "browser_approval_resume_failed": evidence.browser_approval_resume_passed,
        "container_sandbox_smoke_failed": evidence.container_sandbox_passed,
        "server_container_sandbox_not_configured": (evidence.server_container_sandbox_configured),
        "session_profile_rollback_unavailable": evidence.rollback_available,
    }
    blockers.extend(blocker for blocker, passed in external_checks.items() if not passed)
    unique_blockers = tuple(dict.fromkeys(blockers))
    return ReleaseGateDecision(
        ready=not unique_blockers,
        blockers=unique_blockers,
        paired_scenarios=report.paired_scenarios(),
        legacy_metrics=legacy_metrics,
        deerflow_v2_metrics=v2_metrics,
        max_latency_ratio=evidence.max_latency_ratio,
    )


def _append_latency_blocker(
    blockers: list[str],
    *,
    name: str,
    legacy: float | None,
    deerflow_v2: float | None,
    max_ratio: float,
) -> None:
    if legacy is None or deerflow_v2 is None or legacy <= 0 or deerflow_v2 <= 0:
        blockers.append(f"p95_{name}_missing")
        return
    if deerflow_v2 > legacy * max_ratio:
        blockers.append(f"deerflow_v2_p95_{name}_above_legacy")


__all__ = [
    "ReleaseGateDecision",
    "ReleaseGateEvidence",
    "evaluate_release_gate",
]
