"""Small immutable contracts shared by Loop Harness adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

WorkerVerdict = Literal["NO_OP", "FIX", "REPORT", "BLOCKED"]


@dataclass(frozen=True, slots=True)
class WorkerResult:
    verdict: WorkerVerdict
    summary: str
    evidence: tuple[str, ...]
    reproduction: tuple[str, ...]
    changed_files: tuple[str, ...]
    tests: tuple[str, ...]
    risk_reasons: tuple[str, ...]
    suggested_tier: Literal["A", "B", "C"]
    confidence: float


@dataclass(frozen=True, slots=True)
class RunReport:
    run_id: str
    state: str
    error_code: str | None
    summary: str
    base_sha: str | None = None
    notification: str | None = None
