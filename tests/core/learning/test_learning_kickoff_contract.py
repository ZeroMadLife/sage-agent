from __future__ import annotations

from core.coding.turn_input import TurnInputKind
from core.learning.kickoff import LearningKickoffErrorCode


def test_learning_kickoff_turn_kind_withholds_duplicate_user_event() -> None:
    assert TurnInputKind.USER.emits_user_event is True
    assert TurnInputKind.GOAL_FOLLOWUP.emits_user_event is False
    assert TurnInputKind.LEARNING_KICKOFF.emits_user_event is False
    assert TurnInputKind.LEARNING_KICKOFF.input_origin == "learning_kickoff"


def test_learning_kickoff_error_codes_are_a_closed_public_contract() -> None:
    assert LearningKickoffErrorCode.DISPATCH_FAILED.value == "learning_kickoff_dispatch_failed"
    assert LearningKickoffErrorCode.BINDING_CONFLICT.value == "learning_kickoff_binding_conflict"
    assert LearningKickoffErrorCode.JOURNAL_CONFLICT.value == "learning_kickoff_journal_conflict"
