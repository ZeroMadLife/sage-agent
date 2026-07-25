#!/usr/bin/env python3
"""Run the deterministic Sage Memory lifecycle evaluation."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from core.coding.memory import EpisodicEvidence, consolidate_evidence
from core.coding.persistence.memory_store import SCHEMA_VERSION, MemoryCandidate, MemoryStore


@dataclass(frozen=True, slots=True)
class MemoryEvalCase:
    case_id: str
    category: str
    passed: bool
    evidence: str


def run_evaluation() -> dict[str, Any]:
    cases: list[MemoryEvalCase] = []
    with tempfile.TemporaryDirectory(prefix="sage-memory-eval-") as temporary:
        root = Path(temporary)
        for index in range(8):
            cases.append(_run_case("proposal_isolation", index, root, _proposal_isolation))
            cases.append(_run_case("retraction", index, root, _retraction))
            cases.append(_run_case("supersession", index, root, _supersession))
            cases.append(_run_case("consolidation", index, root, _consolidation))
            cases.append(_run_case("restart_and_scope", index, root, _restart_and_scope))

    categories = {
        category: {
            "case_count": len(selected),
            "passed": sum(case.passed for case in selected),
            "pass_rate": sum(case.passed for case in selected) / len(selected),
        }
        for category in sorted({case.category for case in cases})
        for selected in ([case for case in cases if case.category == category],)
    }
    passed = sum(case.passed for case in cases)
    return {
        "evaluation_id": "sage-memory-lifecycle-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_commit": _git_value("rev-parse", "HEAD"),
        "source_dirty": bool(_git_value("status", "--porcelain")),
        "schema_version": SCHEMA_VERSION,
        "case_count": len(cases),
        "passed": passed,
        "failed": len(cases) - passed,
        "pass_rate": passed / len(cases),
        "categories": categories,
        "cases": [asdict(case) for case in cases],
    }


def _run_case(
    category: str,
    index: int,
    root: Path,
    scenario: Callable[[Path, int], str],
) -> MemoryEvalCase:
    case_id = f"{category}-{index + 1:02d}"
    try:
        evidence = scenario(root / case_id, index)
    except Exception as exc:
        return MemoryEvalCase(case_id, category, False, type(exc).__name__)
    return MemoryEvalCase(case_id, category, True, evidence)


def _proposal_isolation(root: Path, index: int) -> str:
    store = MemoryStore(root, "workspace")
    candidate = MemoryCandidate(f"proposal fact {index}")
    proposal = store.create_proposal([candidate], proposal_id=f"proposal-{index}")
    if store.list_facts():
        raise AssertionError("pending proposal became visible")
    store.approve(proposal.proposal_id, proposal.revision)
    if [fact.content for fact in store.list_facts()] != [candidate.content]:
        raise AssertionError("approved fact is missing")
    return "pending hidden; approved active"


def _retraction(root: Path, index: int) -> str:
    store = MemoryStore(root, "workspace")
    candidate = MemoryCandidate(f"retract fact {index}")
    store.create_proposal([candidate], proposal_id=f"retract-{index}")
    store.approve(f"retract-{index}", 0)
    result = store.retract_fact(
        candidate.content_hash,
        expected_revision=1,
        reason="evaluation retraction",
        actor_ref=f"eval-{index}",
    )
    if result.status != "retracted" or result.revision != 2 or store.list_facts():
        raise AssertionError("retracted fact remained active")
    return "active -> retracted@2; active recall empty"


def _supersession(root: Path, index: int) -> str:
    store = MemoryStore(root, "workspace")
    old = MemoryCandidate(f"old decision {index}", topic="decisions")
    new = MemoryCandidate(
        f"new decision {index}",
        topic="decisions",
        source="memory_correction",
        supersedes_content_hash=old.content_hash,
    )
    store.create_proposal([old], proposal_id=f"old-{index}")
    store.approve(f"old-{index}", 0)
    store.create_proposal([new], proposal_id=f"new-{index}")
    store.approve(f"new-{index}", 0)
    stored = {fact.content_hash: fact for fact in store.list_stored_facts()}
    if stored[old.content_hash].status != "superseded":
        raise AssertionError("old fact was not superseded")
    if [fact.content for fact in store.list_facts()] != [new.content]:
        raise AssertionError("replacement is not the only active fact")
    return "old superseded; replacement active"


def _consolidation(root: Path, index: int) -> str:
    store = MemoryStore(root, "workspace")
    existing = MemoryCandidate(f"existing convention {index}")
    store.create_proposal([existing], proposal_id=f"existing-{index}")
    store.approve(f"existing-{index}", 0)
    result = consolidate_evidence(
        [
            EpisodicEvidence(
                f"run-{index}",
                f"  EXISTING convention {index} ",
                evidence_refs=("cite-duplicate",),
            ),
            EpisodicEvidence(
                f"run-{index}",
                f"new sourced convention {index}",
                evidence_refs=("cite-new",),
            ),
            EpisodicEvidence(f"run-{index}", "missing evidence"),
        ],
        store.list_stored_facts("active"),
    )
    if (len(result.candidates), result.duplicate_count, result.rejected_count) != (1, 1, 1):
        raise AssertionError("consolidation gates changed")
    if len(store.list_facts()) != 1:
        raise AssertionError("consolidation mutated active facts")
    return "1 candidate; 1 duplicate; 1 rejected; no mutation"


def _restart_and_scope(root: Path, index: int) -> str:
    first = MemoryStore(root, "workspace-a")
    second = MemoryStore(root, "workspace-b")
    candidate = MemoryCandidate(f"scoped fact {index}")
    first.create_proposal([candidate], proposal_id=f"scope-{index}")
    first.approve(f"scope-{index}", 0)
    reopened = MemoryStore(root, "workspace-a")
    if [fact.content for fact in reopened.list_facts()] != [candidate.content]:
        raise AssertionError("fact did not survive restart")
    if second.list_facts():
        raise AssertionError("fact crossed workspace boundary")
    return "restart preserved; other workspace empty"


def _git_value(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_evaluation()
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
