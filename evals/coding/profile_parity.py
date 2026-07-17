"""Deterministic legacy/deerflow_v2 timeline parity metrics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

RuntimeProfile = Literal["legacy", "deerflow_v2"]
RUNTIME_PROFILES: tuple[RuntimeProfile, ...] = ("legacy", "deerflow_v2")


@dataclass(frozen=True, slots=True)
class ProfileScenarioResult:
    """One scenario projected from the public durable timeline contract."""

    scenario: str
    runtime_profile: RuntimeProfile
    completed: bool
    final_seen: bool
    assertions_passed: bool
    streamed: bool
    tool_calls: int
    tool_errors: int
    policy_compliant: bool
    first_token_ms: float | None
    duration_ms: float

    @property
    def contract_completed(self) -> bool:
        return self.completed and self.final_seen

    @property
    def passed(self) -> bool:
        return (
            self.contract_completed
            and self.assertions_passed
            and self.policy_compliant
        )

    @property
    def tool_success_rate(self) -> float:
        if self.tool_calls == 0:
            return 1.0
        return max(0.0, (self.tool_calls - self.tool_errors) / self.tool_calls)

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "runtime_profile": self.runtime_profile,
            "passed": self.passed,
            "completed": self.completed,
            "final_seen": self.final_seen,
            "contract_completed": self.contract_completed,
            "assertions_passed": self.assertions_passed,
            "streamed": self.streamed,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "tool_success_rate": round(self.tool_success_rate, 4),
            "policy_compliant": self.policy_compliant,
            "first_token_ms": self.first_token_ms,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True, slots=True)
class ProfileMetrics:
    runtime_profile: RuntimeProfile
    scenario_count: int
    task_completion_rate: float
    streaming_rate: float
    tool_call_success_rate: float
    policy_compliance_rate: float
    p50_first_token_ms: float | None
    p95_first_token_ms: float | None
    p50_duration_ms: float
    p95_duration_ms: float

    def to_dict(self) -> dict[str, object]:
        return {
            "runtime_profile": self.runtime_profile,
            "scenario_count": self.scenario_count,
            "task_completion_rate": round(self.task_completion_rate, 4),
            "streaming_rate": round(self.streaming_rate, 4),
            "tool_call_success_rate": round(self.tool_call_success_rate, 4),
            "policy_compliance_rate": round(self.policy_compliance_rate, 4),
            "p50_first_token_ms": self.p50_first_token_ms,
            "p95_first_token_ms": self.p95_first_token_ms,
            "p50_duration_ms": self.p50_duration_ms,
            "p95_duration_ms": self.p95_duration_ms,
        }


@dataclass(slots=True)
class ProfileParityReport:
    """Aggregate comparable scenarios without hiding profile-specific failures."""

    results: list[ProfileScenarioResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        keys = [
            (result.scenario, result.runtime_profile) for result in self.results
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate scenario/runtime_profile result")

    def metrics(self, profile: RuntimeProfile) -> ProfileMetrics:
        selected = [result for result in self.results if result.runtime_profile == profile]
        tool_calls = sum(result.tool_calls for result in selected)
        tool_errors = sum(result.tool_errors for result in selected)
        first_tokens = [
            result.first_token_ms
            for result in selected
            if result.first_token_ms is not None
        ]
        return ProfileMetrics(
            runtime_profile=profile,
            scenario_count=len(selected),
            task_completion_rate=_rate(sum(result.passed for result in selected), len(selected)),
            streaming_rate=_rate(sum(result.streamed for result in selected), len(selected)),
            tool_call_success_rate=(
                _rate(max(0, tool_calls - tool_errors), tool_calls)
                if tool_calls
                else 1.0
            ),
            policy_compliance_rate=_rate(
                sum(result.policy_compliant for result in selected), len(selected)
            ) if selected else 1.0,
            p50_first_token_ms=(
                _percentile(first_tokens, 0.50) if first_tokens else None
            ),
            p95_first_token_ms=(
                _percentile(first_tokens, 0.95) if first_tokens else None
            ),
            p50_duration_ms=_percentile(
                [result.duration_ms for result in selected], 0.50
            ),
            p95_duration_ms=_percentile(
                [result.duration_ms for result in selected], 0.95
            ),
        )

    def paired_scenarios(self) -> tuple[str, ...]:
        legacy = {
            result.scenario for result in self.results if result.runtime_profile == "legacy"
        }
        deerflow = {
            result.scenario
            for result in self.results
            if result.runtime_profile == "deerflow_v2"
        }
        return tuple(sorted(legacy & deerflow))

    def regressions(self) -> tuple[str, ...]:
        by_key = {
            (result.scenario, result.runtime_profile): result for result in self.results
        }
        regressions: list[str] = []
        for scenario in self.paired_scenarios():
            legacy = by_key[(scenario, "legacy")]
            deerflow = by_key[(scenario, "deerflow_v2")]
            if (
                (legacy.passed and not deerflow.passed)
                or (legacy.streamed and not deerflow.streamed)
                or (legacy.policy_compliant and not deerflow.policy_compliant)
                or deerflow.tool_success_rate < legacy.tool_success_rate
            ):
                regressions.append(scenario)
        return tuple(regressions)

    def to_dict(self) -> dict[str, object]:
        return {
            "profiles": {
                profile: self.metrics(profile).to_dict()
                for profile in RUNTIME_PROFILES
            },
            "paired_scenarios": list(self.paired_scenarios()),
            "regressions": list(self.regressions()),
            "results": [result.to_dict() for result in self.results],
        }


def project_profile_timeline(
    scenario: str,
    runtime_profile: RuntimeProfile,
    events: Sequence[Mapping[str, Any]],
    *,
    assertions_passed: bool,
    expected_policy_denial: bool = False,
) -> ProfileScenarioResult:
    """Project only public timeline envelopes; never inspect graph checkpoints."""
    projected = [
        (event, payload)
        for event in events
        if isinstance((payload := event.get("payload", {})), Mapping)
    ]
    payloads = [payload for _, payload in projected]
    start = next(
        (
            _timestamp(event.get("timestamp"))
            for event, payload in projected
            if payload.get("event") == "run_started"
        ),
        None,
    )
    terminal_event = next(
        (event for event in reversed(events) if event.get("kind") == "terminal"),
        None,
    )
    terminal = _timestamp(terminal_event.get("timestamp")) if terminal_event else None
    first_text_event = next(
        (
            event
            for event, payload in projected
            if payload.get("type") == "text_delta"
        ),
        None,
    )
    first_text = _timestamp(first_text_event.get("timestamp")) if first_text_event else None
    tool_calls = sum(payload.get("type") == "tool_call" for payload in payloads)
    tool_errors = sum(
        payload.get("type") == "tool_result" and bool(payload.get("is_error"))
        for payload in payloads
    )
    security_violation = any(
        payload.get("type") == "security_violation" for payload in payloads
    )
    policy_denied = any(
        payload.get("type") == "policy_violation"
        or (
            payload.get("type") == "tool_result"
            and bool(payload.get("is_error"))
            and str(payload.get("policy_reason", "")).strip() != ""
        )
        for payload in payloads
    )
    policy_compliant = not security_violation and (
        policy_denied if expected_policy_denial else not policy_denied
    )
    return ProfileScenarioResult(
        scenario=scenario,
        runtime_profile=runtime_profile,
        completed=bool(terminal_event and terminal_event.get("status") == "completed"),
        final_seen=any(payload.get("type") == "final" for payload in payloads),
        assertions_passed=assertions_passed,
        streamed=first_text_event is not None,
        tool_calls=tool_calls,
        tool_errors=tool_errors,
        policy_compliant=policy_compliant,
        first_token_ms=_duration_ms(start, first_text),
        duration_ms=_duration_ms(start, terminal) or 0.0,
    )


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _duration_ms(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round(max(0.0, (end - start).total_seconds() * 1000), 3)


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(int(len(ordered) * quantile), len(ordered) - 1)
    return round(ordered[index], 3)


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


__all__ = [
    "RUNTIME_PROFILES",
    "ProfileMetrics",
    "ProfileParityReport",
    "ProfileScenarioResult",
    "RuntimeProfile",
    "project_profile_timeline",
]
