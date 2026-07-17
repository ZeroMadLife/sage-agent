"""Benchmark metrics aggregation (4 core metrics per 12.2 simplification).

The four headline metrics are:
- task_completion_rate: share of scenarios whose assertions all pass
- tool_call_success_rate: successful tool calls / total tool calls
- policy_compliance_rate: share of scenarios that stayed policy-compliant
- p95_turn_latency_ms: 95th percentile turn latency across scenarios
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScenarioResult:
    name: str
    category: str
    passed: bool
    tool_calls: int = 0
    tool_errors: int = 0
    policy_compliant: bool = True
    duration_ms: int = 0
    detail: str = ""


@dataclass
class BenchmarkReport:
    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def task_completion_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    @property
    def tool_call_success_rate(self) -> float:
        total = sum(r.tool_calls for r in self.results)
        errors = sum(r.tool_errors for r in self.results)
        if total == 0:
            return 1.0
        return (total - errors) / total

    @property
    def policy_compliance_rate(self) -> float:
        if not self.results:
            return 1.0
        return sum(1 for r in self.results if r.policy_compliant) / len(self.results)

    @property
    def p95_turn_latency_ms(self) -> float:
        if not self.results:
            return 0.0
        latencies = sorted(r.duration_ms for r in self.results)
        idx = int(len(latencies) * 0.95)
        return latencies[min(idx, len(latencies) - 1)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": {
                "task_completion_rate": round(self.task_completion_rate, 4),
                "tool_call_success_rate": round(self.tool_call_success_rate, 4),
                "policy_compliance_rate": round(self.policy_compliance_rate, 4),
                "p95_turn_latency_ms": self.p95_turn_latency_ms,
            },
            "results": [
                {
                    "name": r.name,
                    "category": r.category,
                    "passed": r.passed,
                    "tool_calls": r.tool_calls,
                    "tool_errors": r.tool_errors,
                    "policy_compliant": r.policy_compliant,
                    "duration_ms": r.duration_ms,
                    "detail": r.detail,
                }
                for r in self.results
            ],
        }
