"""Small immutable contracts shared by Loop Harness adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

WorkerVerdict = Literal["NO_OP", "FIX", "FRONTEND_CANDIDATE", "REPORT", "BLOCKED"]
LoopTier = Literal["A", "B", "C"]
ReviewerVerdict = Literal["PASS", "REQUEST_CHANGES", "BLOCK"]


@dataclass(frozen=True, slots=True)
class WorkerResult:
    verdict: WorkerVerdict
    summary: str
    evidence: tuple[str, ...]
    reproduction: tuple[str, ...]
    changed_files: tuple[str, ...]
    tests: tuple[str, ...]
    risk_reasons: tuple[str, ...]
    suggested_tier: LoopTier
    confidence: float


@dataclass(frozen=True, slots=True)
class FixerResult:
    summary: str
    changed_files: tuple[str, ...]
    tests: tuple[str, ...]
    risk_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DiffSnapshot:
    changed_files: tuple[str, ...]
    additions: int
    deletions: int
    binary_files: tuple[str, ...]
    symlink_files: tuple[str, ...]
    behavior_changed: bool
    deleted_files: tuple[str, ...] = ()

    @property
    def changed_lines(self) -> int:
        return self.additions + self.deletions


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    tier: LoopTier
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidationStep:
    name: str
    exit_code: int
    duration_seconds: float


@dataclass(frozen=True, slots=True)
class ValidationResult:
    passed: bool
    steps: tuple[ValidationStep, ...]


@dataclass(frozen=True, slots=True)
class ArtifactReceipt:
    directory: Path
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class PullRequestReceipt:
    number: int
    url: str
    branch: str
    head_sha: str


@dataclass(frozen=True, slots=True)
class ReviewerResult:
    verdict: ReviewerVerdict
    summary: str
    findings: tuple[str, ...]
    tests: str
    visual_evidence: str
    clean_room: str
    merge_recommendation: str


@dataclass(frozen=True, slots=True)
class RunReport:
    run_id: str
    state: str
    error_code: str | None
    summary: str
    base_sha: str | None = None
    notification: str | None = None
