from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.coding.persistence.session_event_journal import (
    SessionEventJournal,
    SessionEventJournalError,
    SessionRunLeaseConflictError,
)
from core.harness.thread_goal import ThreadGoalService
from core.harness.thread_goal_evaluator import (
    GoalCriterionDecision,
    GoalEvaluationDecision,
    build_thread_goal_evaluation_request,
)


def _create_auto_goal(journal: SessionEventJournal, *, maximum: int = 2) -> dict:
    service = ThreadGoalService(journal)
    created = service.upsert(
        description="完成有证据的目标",
        completion_criteria=["至少产生一条可验证证据"],
        expected_revision=0,
    )
    assert created["continuation"]["mode"] == "manual"
    return service.configure_continuation(
        expected_revision=1,
        mode="bounded_auto",
        max_auto_followups=maximum,
    )


def _complete_run(
    journal: SessionEventJournal,
    *,
    run_id: str,
    goal: dict,
    evidence: str | None = None,
    goal_followup: dict | None = None,
) -> None:
    begun = journal.begin_run(
        run_id,
        owner_id="test-owner",
        owner_pid=os.getpid(),
        thread_goal=goal,
        expected_thread_goal_revision=int(goal["revision"]),
        goal_followup=goal_followup,
    )
    if evidence is not None:
        journal.append(
            run_id=run_id,
            kind="tool",
            status="completed",
            payload={
                "type": "tool_result",
                "tool": "read_file",
                "summary": evidence,
                "evidence_ref": f"evidence:{run_id}",
            },
            lease_owner_id="test-owner",
            fencing_token=begun.fencing_token,
        )
    journal.append_terminal_and_release(
        run_id=run_id,
        status="completed",
        payload={"event": "run_completed"},
        lease_owner_id="test-owner",
        fencing_token=begun.fencing_token,
    )


def _continue_decision(*, ref: str | None = None) -> GoalEvaluationDecision:
    refs = (ref,) if ref is not None else ()
    return GoalEvaluationDecision(
        status="continue",
        blocker="goal_not_met_yet",
        evidence_refs=refs,
        next_action="继续收集下一条证据",
        criteria=(GoalCriterionDecision(index=0, status="unmet", evidence_refs=refs),),
    )


def test_post_turn_evaluation_and_reservation_commit_atomically(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    goal = _create_auto_goal(journal)
    _complete_run(journal, run_id="run-source", goal=goal, evidence="first fact")
    request = build_thread_goal_evaluation_request(
        goal=goal,
        run_id="run-source",
        events=journal.events_for_run("run-source"),
    )

    result = ThreadGoalService(journal).evaluate_post_turn(
        request=request,
        decision=_continue_decision(ref="evidence:run-source"),
        terminal_status="completed",
    )

    assert result.goal["revision"] == 3
    assert result.goal["continuation"]["auto_followups_started"] == 1
    pending = journal.pending_thread_goal_followup()
    assert pending is not None
    assert pending["run_id"] == result.reservation["run_id"]
    assert journal.current_thread_goal_revision() == 3
    assert journal.thread_goal_context()["revision"] == 3

    _complete_run(
        journal,
        run_id=str(pending["run_id"]),
        goal=result.goal,
        goal_followup=pending,
    )
    assert journal.pending_thread_goal_followup() is None
    assert journal.run_goal_followup(str(pending["run_id"]))["reservation_id"] == pending[
        "reservation_id"
    ]


def test_two_no_progress_turns_stop_without_another_reservation(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    goal = _create_auto_goal(journal, maximum=4)
    _complete_run(journal, run_id="run-empty-1", goal=goal)
    first_request = build_thread_goal_evaluation_request(
        goal=goal,
        run_id="run-empty-1",
        events=journal.events_for_run("run-empty-1"),
    )
    first = ThreadGoalService(journal).evaluate_post_turn(
        request=first_request,
        decision=_continue_decision(),
        terminal_status="completed",
    )
    assert first.reservation is not None
    _complete_run(
        journal,
        run_id=str(first.reservation["run_id"]),
        goal=first.goal,
        goal_followup=first.reservation,
    )
    second_request = build_thread_goal_evaluation_request(
        goal=first.goal,
        run_id=str(first.reservation["run_id"]),
        events=journal.events_for_run(str(first.reservation["run_id"])),
    )

    second = ThreadGoalService(journal).evaluate_post_turn(
        request=second_request,
        decision=_continue_decision(),
        terminal_status="completed",
    )

    assert second.reservation is None
    assert second.goal["status"] == "blocked"
    assert second.goal["evaluation"]["blocker"] == "no_progress"
    assert second.goal["continuation"]["no_progress_streak"] == 2


def test_user_event_supersedes_a_pending_followup(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    goal = _create_auto_goal(journal)
    _complete_run(journal, run_id="run-source", goal=goal, evidence="first fact")
    request = build_thread_goal_evaluation_request(
        goal=goal,
        run_id="run-source",
        events=journal.events_for_run("run-source"),
    )
    result = ThreadGoalService(journal).evaluate_post_turn(
        request=request,
        decision=_continue_decision(ref="evidence:run-source"),
        terminal_status="completed",
    )
    assert result.reservation is not None

    journal.append(
        run_id="run-user",
        kind="user",
        status="completed",
        payload={"type": "user", "content": "我来调整下一步"},
    )

    assert journal.pending_thread_goal_followup() is None


def test_pending_followup_survives_journal_reopen_and_is_consumed_once(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    goal = _create_auto_goal(journal)
    _complete_run(journal, run_id="run-source", goal=goal, evidence="first fact")
    request = build_thread_goal_evaluation_request(
        goal=goal,
        run_id="run-source",
        events=journal.events_for_run("run-source"),
    )
    result = ThreadGoalService(journal).evaluate_post_turn(
        request=request,
        decision=_continue_decision(ref="evidence:run-source"),
        terminal_status="completed",
    )
    assert result.reservation is not None

    reopened = SessionEventJournal(tmp_path, "session-1")
    pending = reopened.pending_thread_goal_followup()
    assert pending is not None
    _complete_run(
        reopened,
        run_id=str(pending["run_id"]),
        goal=result.goal,
        goal_followup=pending,
    )
    assert SessionEventJournal(tmp_path, "session-1").pending_thread_goal_followup() is None


def test_two_restarted_coordinators_cannot_consume_the_same_followup(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    goal = _create_auto_goal(journal)
    _complete_run(journal, run_id="run-source", goal=goal, evidence="first fact")
    request = build_thread_goal_evaluation_request(
        goal=goal,
        run_id="run-source",
        events=journal.events_for_run("run-source"),
    )
    result = ThreadGoalService(journal).evaluate_post_turn(
        request=request,
        decision=_continue_decision(ref="evidence:run-source"),
        terminal_status="completed",
    )
    assert result.reservation is not None
    first = SessionEventJournal(tmp_path, "session-1")
    second = SessionEventJournal(tmp_path, "session-1")
    pending = first.pending_thread_goal_followup()
    assert pending is not None

    begun = first.begin_run(
        str(pending["run_id"]),
        owner_id="first-owner",
        owner_pid=os.getpid(),
        thread_goal=result.goal,
        expected_thread_goal_revision=int(result.goal["revision"]),
        goal_followup=pending,
    )
    assert second.pending_thread_goal_followup() is None
    with pytest.raises(SessionRunLeaseConflictError):
        second.begin_run(
            str(pending["run_id"]),
            owner_id="second-owner",
            owner_pid=os.getpid(),
            thread_goal=result.goal,
            expected_thread_goal_revision=int(result.goal["revision"]),
            goal_followup=pending,
        )
    first.append_terminal_and_release(
        run_id=str(pending["run_id"]),
        status="completed",
        payload={"event": "run_completed"},
        lease_owner_id="first-owner",
        fencing_token=begun.fencing_token,
    )
    starts = [
        event
        for event in second.events_for_run(str(pending["run_id"]))
        if event.payload.get("event") == "run_started"
    ]
    assert len(starts) == 1


def test_followup_run_rejects_a_tampered_reservation_receipt(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    goal = _create_auto_goal(journal)
    _complete_run(journal, run_id="run-source", goal=goal, evidence="first fact")
    request = build_thread_goal_evaluation_request(
        goal=goal,
        run_id="run-source",
        events=journal.events_for_run("run-source"),
    )
    result = ThreadGoalService(journal).evaluate_post_turn(
        request=request,
        decision=_continue_decision(ref="evidence:run-source"),
        terminal_status="completed",
    )
    assert result.reservation is not None
    tampered = {**result.reservation, "prompt": "run something else"}

    with pytest.raises(SessionEventJournalError, match="prompt does not match"):
        journal.begin_run(
            str(tampered["run_id"]),
            owner_id="test-owner",
            owner_pid=os.getpid(),
            thread_goal=result.goal,
            expected_thread_goal_revision=int(result.goal["revision"]),
            goal_followup=tampered,
        )


def test_auto_followup_limit_stops_even_when_new_evidence_arrives(tmp_path: Path) -> None:
    journal = SessionEventJournal(tmp_path, "session-1")
    goal = _create_auto_goal(journal, maximum=1)
    _complete_run(journal, run_id="run-source", goal=goal, evidence="first fact")
    first_request = build_thread_goal_evaluation_request(
        goal=goal,
        run_id="run-source",
        events=journal.events_for_run("run-source"),
    )
    first = ThreadGoalService(journal).evaluate_post_turn(
        request=first_request,
        decision=_continue_decision(ref="evidence:run-source"),
        terminal_status="completed",
    )
    assert first.reservation is not None
    _complete_run(
        journal,
        run_id=str(first.reservation["run_id"]),
        goal=first.goal,
        evidence="second distinct fact",
        goal_followup=first.reservation,
    )
    second_request = build_thread_goal_evaluation_request(
        goal=first.goal,
        run_id=str(first.reservation["run_id"]),
        events=journal.events_for_run(str(first.reservation["run_id"])),
    )

    second = ThreadGoalService(journal).evaluate_post_turn(
        request=second_request,
        decision=_continue_decision(ref=f"evidence:{first.reservation['run_id']}"),
        terminal_status="completed",
    )

    assert second.reservation is None
    assert second.goal["status"] == "active"
    assert second.goal["continuation"]["stop_reason"] == "max_auto_followups"
