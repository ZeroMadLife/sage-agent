"""Coding tool permission and policy tests."""

from pathlib import Path

from core.coding.context import WorkspaceContext
from core.coding.tool_executor import PermissionChecker, ToolPolicyChecker
from core.coding.tools.base import RegisteredTool, ToolResult
from core.coding.tools.registry import build_tool_registry


def _tools(tmp_path: Path) -> tuple[WorkspaceContext, dict[str, RegisteredTool]]:
    workspace = WorkspaceContext(root=tmp_path)
    return workspace, build_tool_registry(workspace)


def test_permission_allows_read_only_tools_without_approval(tmp_path: Path) -> None:
    """Read-only tools are always allowed."""
    workspace, tools = _tools(tmp_path)
    checker = PermissionChecker(approval_policy="never")

    decision = checker.check(tools["read_file"], {"path": "README.md"}, workspace)

    assert decision.allowed is True
    assert decision.reason == "read_only"


def test_permission_denies_risky_tools_when_policy_is_never(tmp_path: Path) -> None:
    """Risky tools are denied when approval policy is never."""
    workspace, tools = _tools(tmp_path)
    checker = PermissionChecker(approval_policy="never")

    decision = checker.check(tools["write_file"], {"path": "x.txt"}, workspace)

    assert decision.allowed is False
    assert decision.security_event_type == "approval_denied"


def test_permission_respects_tool_approval_metadata_in_ask_mode(tmp_path: Path) -> None:
    """Risky tools can opt out of approval when metadata marks them as governed elsewhere."""
    workspace = WorkspaceContext(root=tmp_path)
    tool = RegisteredTool(
        name="safe_risky_tool",
        schema={},
        description="Risky implementation, but pre-governed by a stricter policy layer.",
        risky=True,
        requires_approval=False,
        runner=lambda _args: ToolResult(content="ok"),
    )
    checker = PermissionChecker(approval_policy="ask")

    decision = checker.check(tool, {}, workspace)

    assert decision.allowed is True
    assert decision.reason == "approval_not_required"


def test_permission_enforces_plan_mode_read_only_guard(tmp_path: Path) -> None:
    """Plan mode allows reads but rejects writes and shell execution."""
    workspace, tools = _tools(tmp_path)
    checker = PermissionChecker(approval_policy="auto", plan_mode=True)

    read_decision = checker.check(tools["read_file"], {"path": "README.md"}, workspace)
    write_decision = checker.check(tools["write_file"], {"path": "x.txt"}, workspace)
    shell_decision = checker.check(tools["run_shell"], {"command": "echo hi"}, workspace)

    assert read_decision.allowed is True
    assert write_decision.allowed is False
    assert shell_decision.allowed is False
    assert write_decision.security_event_type == "plan_mode_write_guard"


def test_permission_enforces_write_scope(tmp_path: Path) -> None:
    """Workers can write only inside their configured write scope."""
    workspace, tools = _tools(tmp_path)
    checker = PermissionChecker(approval_policy="auto", write_scope=["allowed"])

    inside = checker.check(tools["write_file"], {"path": "allowed/a.txt"}, workspace)
    outside = checker.check(tools["write_file"], {"path": "other/a.txt"}, workspace)

    assert inside.allowed is True
    assert outside.allowed is False
    assert outside.security_event_type == "write_scope_guard"


def test_policy_requires_fresh_read_before_patch_or_overwrite(tmp_path: Path) -> None:
    """Modifying an existing file requires a fresh read_file first."""
    target = tmp_path / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    workspace, tools = _tools(tmp_path)
    checker = ToolPolicyChecker(workspace)

    before_read = checker.check(
        tools["patch_file"],
        {"path": "app.py", "old_text": "value = 1", "new_text": "value = 2"},
    )
    workspace.mark_read("app.py")
    after_read = checker.check(
        tools["patch_file"],
        {"path": "app.py", "old_text": "value = 1", "new_text": "value = 2"},
    )

    assert before_read.allowed is False
    assert before_read.reason == "prior_read_required"
    assert after_read.allowed is True


def test_policy_rejects_shell_search_at_command_start_but_allows_pipe_tail(
    tmp_path: Path,
) -> None:
    """Shell search/read commands are rejected only at command boundaries."""
    workspace, tools = _tools(tmp_path)
    checker = ToolPolicyChecker(workspace)

    grep_decision = checker.check(tools["run_shell"], {"command": "grep -R alpha ."})
    pipe_decision = checker.check(tools["run_shell"], {"command": "pytest -q | tail -5"})

    assert grep_decision.allowed is False
    assert "use search" in grep_decision.message
    assert pipe_decision.allowed is True
