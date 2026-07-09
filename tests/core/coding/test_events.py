"""Typed coding run event tests."""

from core.coding.engine import (
    ApprovalRequiredEvent,
    CancelledEvent,
    FinalEvent,
    PlanReadyForReviewEvent,
    RuntimeModeChangedEvent,
    StepLimitEvent,
    ToolCallEvent,
    ToolResultEvent,
    event_to_dict,
)


def test_tool_call_and_result_are_json_safe() -> None:
    """Tool events serialize to the existing dict wire shape."""
    call = ToolCallEvent(run_id="run_1", tool="read_file", args={"path": "README.md"})
    result = ToolResultEvent(
        run_id="run_1",
        tool="read_file",
        args={"path": "README.md"},
        content="# Sage",
        is_error=False,
    )

    assert event_to_dict(call)["type"] == "tool_call"
    assert event_to_dict(result) == {
        "type": "tool_result",
        "run_id": "run_1",
        "created_at": result.created_at,
        "tool": "read_file",
        "args": {"path": "README.md"},
        "content": "# Sage",
        "is_error": False,
        "policy_reason": None,
        "security_event_type": None,
    }


def test_events_have_default_run_id_and_created_at() -> None:
    """Base event defaults are available even before runtime stamps run_id."""
    event = FinalEvent(content="done")

    assert event.run_id == ""
    assert event.created_at
    assert event_to_dict(event)["created_at"] == event.created_at


def test_approval_event_fields_are_stable() -> None:
    """Approval events expose the UI contract used by the workbench."""
    event = ApprovalRequiredEvent(
        run_id="run_1",
        approval_id="appr_1",
        tool="write_file",
        args={"path": "note.txt"},
        description="write_file requires approval.",
        pattern_key="tool:write_file",
    )

    data = event_to_dict(event)

    assert data["approval_id"] == "appr_1"
    assert data["tool"] == "write_file"
    assert data["pattern_key"] == "tool:write_file"


def test_terminal_events_keep_content() -> None:
    """Final, cancelled, and step_limit events keep user-visible content."""
    assert event_to_dict(FinalEvent(content="ok"))["content"] == "ok"
    assert event_to_dict(CancelledEvent(content="stopped"))["content"] == "stopped"
    assert event_to_dict(StepLimitEvent(content="limit"))["content"] == "limit"


def test_policy_and_security_fields_are_optional_and_serializable() -> None:
    """Tool result errors can carry policy/security metadata without breaking JSON."""
    event = ToolResultEvent(
        tool="patch_file",
        args={"path": "app.py"},
        content="blocked",
        is_error=True,
        policy_reason="prior_read_required",
        security_event_type="write_scope_guard",
    )

    data = event_to_dict(event)

    assert data["policy_reason"] == "prior_read_required"
    assert data["security_event_type"] == "write_scope_guard"


def test_runtime_mode_changed_event_serializes_plan_state() -> None:
    """Runtime mode change events carry mode, topic, and plan_path to the UI."""
    event = RuntimeModeChangedEvent(
        run_id="run_1",
        mode="plan",
        topic="Refactor API",
        plan_path=".coding/plans/refactor-api-plan.md",
    )

    data = event_to_dict(event)

    assert data == {
        "type": "runtime_mode_changed",
        "run_id": "run_1",
        "created_at": event.created_at,
        "mode": "plan",
        "topic": "Refactor API",
        "plan_path": ".coding/plans/refactor-api-plan.md",
    }


def test_runtime_mode_changed_event_defaults_to_default_mode() -> None:
    """A freshly constructed mode event defaults to default mode with empty plan."""
    event = RuntimeModeChangedEvent()

    assert event.type == "runtime_mode_changed"
    assert event.mode == "default"
    assert event.topic == ""
    assert event.plan_path == ""


def test_plan_ready_for_review_event_serializes_review_payload() -> None:
    """Plan review events carry review_id, plan_path, and summary to the UI."""
    event = PlanReadyForReviewEvent(
        run_id="run_1",
        review_id="plan_review_1",
        plan_path=".coding/plans/refactor-api-plan.md",
        summary="# Refactor plan\nstep 1",
    )

    data = event_to_dict(event)

    assert data == {
        "type": "plan_ready_for_review",
        "run_id": "run_1",
        "created_at": event.created_at,
        "review_id": "plan_review_1",
        "plan_path": ".coding/plans/refactor-api-plan.md",
        "summary": "# Refactor plan\nstep 1",
    }


def test_plan_ready_for_review_event_defaults_to_empty_fields() -> None:
    """A freshly constructed review event defaults to empty review fields."""
    event = PlanReadyForReviewEvent()

    assert event.type == "plan_ready_for_review"
    assert event.review_id == ""
    assert event.plan_path == ""
    assert event.summary == ""
