"""Plan review approval gate for plan mode exit."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Literal

from core.coding.context import now

PlanReviewResult = Literal["pending", "approved", "rejected"]


@dataclass
class PlanReviewEntry:
    """A pending plan review request."""

    review_id: str
    plan_path: str
    summary: str
    created_at: str = field(default_factory=now)
    event: threading.Event = field(default_factory=threading.Event)
    result: PlanReviewResult = "pending"


class PlanReviewManager:
    """Manage plan review lifecycle (submit -> wait -> approve/reject).

    Only one plan review can be pending per runtime at a time. Submitting a new
    review replaces (and implicitly rejects) any existing pending review, since a
    fresh ``exit_plan_mode`` call means the model rewrote the plan.
    """

    def __init__(self) -> None:
        self._entry: PlanReviewEntry | None = None
        self._lock = threading.Lock()

    @property
    def pending(self) -> PlanReviewEntry | None:
        """Return the currently pending review entry, if any."""
        with self._lock:
            return self._entry

    def submit(self, plan_path: str, summary: str) -> PlanReviewEntry:
        """Submit a plan for review. Replaces any existing pending review."""
        with self._lock:
            # If a previous review is still outstanding (e.g. the model called
            # exit_plan_mode again before the user responded), mark it rejected
            # so any waiter unblocks, then replace it with the fresh request.
            if self._entry is not None and not self._entry.event.is_set():
                self._entry.result = "rejected"
                self._entry.event.set()
            entry = PlanReviewEntry(
                review_id=_review_id(),
                plan_path=plan_path,
                summary=summary,
            )
            self._entry = entry
            return entry

    def resolve(self, result: Literal["approved", "rejected"]) -> PlanReviewEntry | None:
        """Resolve the pending plan review and wake any waiter.

        Returns the resolved entry, or ``None`` if no review was pending.
        """
        with self._lock:
            entry = self._entry
            if entry is None:
                return None
            entry.result = result
            entry.event.set()
            self._entry = None
            return entry

    def cancel(self) -> None:
        """Cancel any pending review (e.g. on stop)."""
        with self._lock:
            if self._entry is not None:
                self._entry.result = "rejected"
                self._entry.event.set()
                self._entry = None


def _review_id() -> str:
    """Build a deterministic-enough review id from the current timestamp."""
    return f"plan_review_{now().replace(':', '').replace('-', '')}"
