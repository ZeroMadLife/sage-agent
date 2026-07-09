"""Typed coding run event tests."""

from core.coding.engine import (
    ApprovalRequiredEvent,
    CancelledEvent,
    FinalEvent,
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
