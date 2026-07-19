"""Approval manager and dangerous command tests."""

import pytest

from core.coding.tool_executor import ApprovalManager, check_dangerous_command


def test_approval_manager_submit_resolve_and_pending() -> None:
    """Approval manager exposes one pending entry and resolves it by id."""
    manager = ApprovalManager()
    entry = manager.submit(
        "s1",
        "run_shell",
        {"command": "rm -rf build"},
        "Recursive delete command requires approval.",
        "shell:rm_recursive",
    )

    assert manager.pending("s1") == entry.to_dict()
    assert manager.resolve("s1", entry.approval_id, "once") is True
    assert entry.event.is_set()
    assert entry.result == "once"
    assert manager.pending("s1") is None


def test_approval_manager_tracks_session_approval() -> None:
    """Session approval remembers the approved risk pattern."""
    manager = ApprovalManager()
    entry = manager.submit("s1", "run_shell", {}, "sudo command requires approval.", "shell:sudo")

    assert manager.is_session_approved("s1", "shell:sudo") is False
    assert manager.resolve("s1", entry.approval_id, "session") is True
    assert manager.is_session_approved("s1", "shell:sudo") is True


@pytest.mark.parametrize("tool", ["knowledge_learn", "remember"])
def test_durable_learning_cannot_be_approved_for_the_whole_session(tool: str) -> None:
    """Every durable knowledge or memory write keeps its own confirmation boundary."""
    manager = ApprovalManager()
    pattern_key = f"tool:{tool}"
    entry = manager.submit(
        "s1",
        tool,
        {"topic": "Harness"},
        "Persist cited evidence.",
        pattern_key,
    )

    assert manager.resolve("s1", entry.approval_id, "session") is True
    assert entry.result == "once"
    assert manager.is_session_approved("s1", pattern_key) is False


def test_approval_manager_cancel_session_denies_pending_entries() -> None:
    """Cancelling a session wakes pending approvals as denied."""
    manager = ApprovalManager()
    entry = manager.submit("s1", "write_file", {}, "write_file requires approval.", "tool:write")

    manager.cancel_session("s1")

    assert entry.event.is_set()
    assert entry.result == "deny"
    assert manager.pending("s1") is None


def test_graph_approval_resolution_is_replayable_after_cancel() -> None:
    manager = ApprovalManager()
    entry = manager.submit(
        "s1",
        "write_file",
        {"path": "note.txt"},
        "write_file requires approval.",
        "tool:write_file",
        approval_id="appr_graph_1",
    )

    manager.cancel_session("s1")

    assert entry.result == "deny"
    assert manager.consume_resolution("s1", "appr_graph_1") == "deny"
    assert manager.consume_resolution("s1", "appr_graph_1") is None


def test_graph_approval_restores_run_binding_from_durable_payload() -> None:
    manager = ApprovalManager()

    entry = manager.restore_pending(
        {
            "session_id": "s1",
            "run_id": "run-1",
            "approval_id": "appr_graph_1",
            "tool": "write_file",
            "args": {"path": "note.txt"},
            "description": "write_file requires approval.",
            "pattern_key": "tool:write_file",
        }
    )

    assert manager.pending("s1") == entry.to_dict()
    assert manager.run_id_for("s1", entry.approval_id) == "run-1"
    assert manager.resolve("s1", entry.approval_id, "once") is True
    assert manager.consume_resolution("s1", entry.approval_id) == "once"
    assert manager.run_id_for("s1", entry.approval_id) is None


def test_check_dangerous_command_detects_common_patterns() -> None:
    """Common destructive shell commands are classified for approval."""
    dangerous, description, pattern_key = check_dangerous_command("git reset --hard HEAD")

    assert dangerous is True
    assert "reset" in description.lower()
    assert pattern_key == "git_reset_hard"


def test_check_dangerous_command_allows_plain_commands() -> None:
    """Plain read-only shell commands are not marked dangerous."""
    dangerous, description, pattern_key = check_dangerous_command("pytest -q")

    assert dangerous is False
    assert description == ""
    assert pattern_key == ""
