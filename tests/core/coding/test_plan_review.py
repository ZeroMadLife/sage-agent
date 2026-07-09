"""PlanReviewManager unit tests."""

from core.coding.plan_review import PlanReviewEntry, PlanReviewManager


def test_submit_exposes_pending_entry() -> None:
    """submit() registers a pending review retrievable via pending."""
    manager = PlanReviewManager()
    entry = manager.submit(".coding/plans/refactor-api-plan.md", "plan summary")

    assert isinstance(entry, PlanReviewEntry)
    assert entry.review_id.startswith("plan_review_")
    assert entry.plan_path == ".coding/plans/refactor-api-plan.md"
    assert entry.summary == "plan summary"
    assert entry.result == "pending"
    assert not entry.event.is_set()
    assert manager.pending is entry


def test_resolve_approved_clears_pending_and_wakes_waiter() -> None:
    """resolve('approved') sets the entry result, wakes the event, clears pending."""
    manager = PlanReviewManager()
    entry = manager.submit("p.md", "s")

    resolved = manager.resolve("approved")

    assert resolved is entry
    assert entry.result == "approved"
    assert entry.event.is_set()
    assert manager.pending is None


def test_resolve_rejected_keeps_plan_unapproved() -> None:
    """resolve('rejected') marks the entry rejected and clears pending."""
    manager = PlanReviewManager()
    entry = manager.submit("p.md", "s")

    resolved = manager.resolve("rejected")

    assert resolved is entry
    assert entry.result == "rejected"
    assert entry.event.is_set()
    assert manager.pending is None


def test_resolve_returns_none_when_nothing_pending() -> None:
    """resolve() is a no-op (returns None) when no review is pending."""
    manager = PlanReviewManager()

    assert manager.resolve("approved") is None
    assert manager.resolve("rejected") is None


def test_submit_replaces_existing_pending_review() -> None:
    """A fresh submit rejects and unblocks an outstanding review, then registers the new one."""
    manager = PlanReviewManager()
    first = manager.submit("old.md", "old summary")
    second = manager.submit("new.md", "new summary")

    # The previous review is implicitly rejected and its waiter unblocked.
    assert first.result == "rejected"
    assert first.event.is_set()
    # The new review is the active pending entry.
    assert manager.pending is second
    assert second.plan_path == "new.md"
    assert second.result == "pending"


def test_cancel_rejects_and_clears_pending_review() -> None:
    """cancel() rejects an outstanding review (e.g. on stop) and clears pending."""
    manager = PlanReviewManager()
    entry = manager.submit("p.md", "s")

    manager.cancel()

    assert entry.result == "rejected"
    assert entry.event.is_set()
    assert manager.pending is None


def test_cancel_is_a_noop_when_nothing_pending() -> None:
    """cancel() does not raise when there is nothing to cancel."""
    manager = PlanReviewManager()

    manager.cancel()

    assert manager.pending is None


def test_review_entry_has_created_at() -> None:
    """Each review entry is stamped with a created_at timestamp."""
    manager = PlanReviewManager()
    entry = manager.submit("p.md", "s")

    assert entry.created_at
