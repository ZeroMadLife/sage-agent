"""Learning post-turn Goal evaluator 的 canonical scope 重验合同。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tests.core.harness.learning_scope_support import make_real_scope

from api.coding import _post_turn_goal_followup
from core.coding.persistence import TurnPlanStore
from core.coding.persistence.session_event_journal import SessionEventJournal
from core.coding.runtime import CodingRuntime
from core.harness.thread_goal import ThreadGoalService


@pytest.mark.asyncio
async def test_post_turn_learning_goal_revalidates_scope_before_evaluator(
    tmp_path: Path,
) -> None:
    scope, resolver, session, storage, kickoff_run_id = make_real_scope(tmp_path)
    runtime = CodingRuntime(
        session_id=scope.session_id,
        workspace_root=tmp_path / "workspace",
        model=object(),
        storage_root=storage,
        session_state=session,
        runtime_profile="deerflow_v2",
    )
    journal = SessionEventJournal(storage, scope.session_id)
    goal = ThreadGoalService(journal).get()
    assert goal is not None
    source_run_id = "run-goal-evaluation"
    journal.append(
        run_id=source_run_id,
        kind="system",
        status="running",
        payload={"type": "run_started", "thread_goal": goal},
    )
    journal.append_terminal_once(
        run_id=source_run_id,
        status="completed",
        payload={"event": "run_completed"},
    )
    plan_store = TurnPlanStore(storage, scope.session_id)
    with sqlite3.connect(plan_store.path) as connection:
        connection.execute(
            "UPDATE turn_context_plans SET plan_hash = ? WHERE run_id = ?",
            ("sha256:tampered-before-goal-evaluator", kickoff_run_id),
        )
        connection.commit()
    evaluator = MagicMock()
    app = SimpleNamespace(
        state=SimpleNamespace(
            coding_sessions={scope.session_id: runtime},
            coding_run_registry=SimpleNamespace(
                get=lambda session_id: SimpleNamespace(journal=journal)
            ),
            learning_readonly_scope_resolver=resolver,
            coding_goal_evaluator_factory=evaluator,
            coding_goal_followup_tasks=set(),
            coding_goal_followup_shutdown=True,
            mastery_ledger=None,
        )
    )

    await _post_turn_goal_followup(app, scope.session_id, source_run_id)

    evaluator.assert_not_called()
    failures = [
        event.payload
        for event in journal.events_for_run(source_run_id)
        if event.payload.get("type") == "learning_scope_rejected"
    ]
    assert failures[-1]["reason_code"] == "learning_scope_validation_failed"
