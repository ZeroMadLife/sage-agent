from dataclasses import replace
from pathlib import Path

import pytest

from core.learning import (
    MasteryCapability,
    MasteryEvidenceInput,
    MasteryLedger,
    MasteryLedgerConflictError,
)


def _evidence(*, evidence_id: str, kind: str, result: str, created_at: str) -> MasteryEvidenceInput:
    return MasteryEvidenceInput(
        owner_id="owner-a",
        evidence_id=evidence_id,
        workspace_id="workspace-a",
        learning_goal_id="langgraph",
        learning_goal_revision="kgoal-1",
        capability_id="checkpoint",
        kind=kind,  # type: ignore[arg-type]
        result=result,  # type: ignore[arg-type]
        source_ref=f"subagent://run/{evidence_id}",
        source_evidence_id=evidence_id,
        session_id="session-1",
        run_id="run-1",
        summary=f"{kind} {result}",
        metadata={"source": kind},
        created_at=created_at,
    )


def test_projection_requires_two_positive_evidence_kinds(tmp_path: Path) -> None:
    ledger = MasteryLedger(tmp_path / "mastery.sqlite3")
    ledger.record(
        _evidence(
            evidence_id="e1",
            kind="code_test",
            result="pass",
            created_at="2026-07-20T00:00:01+00:00",
        )
    )
    partial = ledger.project(
        owner_id="owner-a",
        workspace_id="workspace-a",
        learning_goal_id="langgraph",
        learning_goal_revision="kgoal-1",
        capabilities=(MasteryCapability("checkpoint", "Checkpoint", 1.0),),
    )
    assert partial.score == 0.5
    assert partial.status == "in_progress"
    assert partial.capabilities[0].status == "developing"

    ledger.record(
        _evidence(
            evidence_id="e2", kind="quiz", result="pass", created_at="2026-07-20T00:00:02+00:00"
        )
    )
    complete = ledger.project(
        owner_id="owner-a",
        workspace_id="workspace-a",
        learning_goal_id="langgraph",
        learning_goal_revision="kgoal-1",
        capabilities=(MasteryCapability("checkpoint", "Checkpoint", 1.0),),
    )
    assert complete.score == 0.95
    assert complete.status == "demonstrated"
    assert complete.capabilities[0].positive_kinds == ("code_test", "quiz")


def test_latest_signal_and_invalidation_recompute_without_duplicates(tmp_path: Path) -> None:
    ledger = MasteryLedger(tmp_path / "mastery.sqlite3")
    first = ledger.record(
        _evidence(
            evidence_id="e1",
            kind="code_test",
            result="pass",
            created_at="2026-07-20T00:00:01+00:00",
        )
    )
    assert (
        ledger.record(
            _evidence(
                evidence_id="e1",
                kind="code_test",
                result="pass",
                created_at="2026-07-20T00:00:01+00:00",
            )
        )
        == first
    )
    with pytest.raises(MasteryLedgerConflictError):
        ledger.record(
            _evidence(
                evidence_id="e1",
                kind="code_test",
                result="fail",
                created_at="2026-07-20T00:00:01+00:00",
            )
        )

    latest = ledger.record(
        _evidence(
            evidence_id="e2",
            kind="code_test",
            result="fail",
            created_at="2026-07-20T00:00:02+00:00",
        )
    )
    before = ledger.project(
        owner_id="owner-a",
        workspace_id="workspace-a",
        learning_goal_id="langgraph",
        learning_goal_revision="kgoal-1",
        capabilities=(MasteryCapability("checkpoint", "Checkpoint", 1.0),),
    )
    assert before.score == 0.0
    invalidated = ledger.invalidate(
        owner_id="owner-a",
        workspace_id="workspace-a",
        evidence_id=latest.evidence_id,
        expected_revision=1,
        reason="测试命令使用了错误的 fixture",
    )
    assert invalidated.status == "invalidated"
    with pytest.raises(MasteryLedgerConflictError):
        ledger.invalidate(
            owner_id="owner-a",
            workspace_id="workspace-a",
            evidence_id=latest.evidence_id,
            expected_revision=1,
            reason="stale retry",
        )
    restored = ledger.project(
        owner_id="owner-a",
        workspace_id="workspace-a",
        learning_goal_id="langgraph",
        learning_goal_revision="kgoal-1",
        capabilities=(MasteryCapability("checkpoint", "Checkpoint", 1.0),),
    )
    assert restored.score == 0.5
    assert restored.evidence[0].status == "invalidated"


def test_projection_isolated_by_workspace_and_goal_revision(tmp_path: Path) -> None:
    ledger = MasteryLedger(tmp_path / "mastery.sqlite3")
    ledger.record(
        _evidence(
            evidence_id="e1",
            kind="code_test",
            result="pass",
            created_at="2026-07-20T00:00:01+00:00",
        )
    )
    assert (
        ledger.project(
            owner_id="owner-a",
            workspace_id="workspace-b",
            learning_goal_id="langgraph",
            learning_goal_revision="kgoal-1",
            capabilities=(MasteryCapability("checkpoint", "Checkpoint", 1.0),),
        ).status
        == "unverified"
    )


def test_record_many_is_atomic_when_one_evidence_conflicts(tmp_path: Path) -> None:
    ledger = MasteryLedger(tmp_path / "mastery.sqlite3")
    ledger.record(
        _evidence(
            evidence_id="existing",
            kind="code_test",
            result="pass",
            created_at="2026-07-20T00:00:01+00:00",
        )
    )

    with pytest.raises(MasteryLedgerConflictError):
        ledger.record_many(
            (
                _evidence(
                    evidence_id="new-item",
                    kind="quiz",
                    result="pass",
                    created_at="2026-07-20T00:00:02+00:00",
                ),
                _evidence(
                    evidence_id="existing",
                    kind="code_test",
                    result="fail",
                    created_at="2026-07-20T00:00:01+00:00",
                ),
            )
        )

    items = ledger.list_evidence(
        owner_id="owner-a",
        workspace_id="workspace-a",
        learning_goal_id="langgraph",
        learning_goal_revision="kgoal-1",
    )
    assert [item.evidence_id for item in items] == ["existing"]
    assert (
        ledger.project(
            owner_id="owner-a",
            workspace_id="workspace-a",
            learning_goal_id="langgraph",
            learning_goal_revision="kgoal-2",
            capabilities=(MasteryCapability("checkpoint", "Checkpoint", 1.0),),
        ).status
        == "unverified"
    )


def test_optional_capabilities_can_complete_and_source_refs_are_opaque(tmp_path: Path) -> None:
    ledger = MasteryLedger(tmp_path / "mastery.sqlite3")
    first = _evidence(
        evidence_id="e1",
        kind="code_test",
        result="pass",
        created_at="2026-07-20T00:00:01+00:00",
    )
    second = _evidence(
        evidence_id="e2",
        kind="quiz",
        result="pass",
        created_at="2026-07-20T00:00:02+00:00",
    )
    ledger.record_many(
        (
            replace(first, source_ref="https://example.com/doc?q=1#part"),
            second,
        )
    )

    projection = ledger.project(
        owner_id="owner-a",
        workspace_id="workspace-a",
        learning_goal_id="langgraph",
        learning_goal_revision="kgoal-1",
        capabilities=(MasteryCapability("checkpoint", "Checkpoint", 1.0, required=False),),
    )

    assert projection.status == "demonstrated"
    assert projection.evidence[-1].source_ref == "https://example.com/doc?q=1#part"
