"""Closed input kinds for one Coding/Harness turn."""

from __future__ import annotations

from enum import StrEnum


class TurnInputKind(StrEnum):
    """Bind input provenance to its public Timeline behavior."""

    USER = "user"
    GOAL_FOLLOWUP = "goal_followup"
    LEARNING_KICKOFF = "learning_kickoff"

    @property
    def input_origin(self) -> str:
        return self.value

    @property
    def emits_user_event(self) -> bool:
        return self is TurnInputKind.USER

    @property
    def emits_goal_followup_event(self) -> bool:
        return self is TurnInputKind.GOAL_FOLLOWUP


__all__ = ["TurnInputKind"]
