"""Coding tool permission and policy tests."""

from pathlib import Path

import pytest

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


def test_permission_mode_default_requires_approval(tmp_path: Path) -> None:
    """default mode requests approval for risky write tools."""
    workspace, tools = _tools(tmp_path)
    checker = PermissionChecker(permission_mode="default")

    decision = checker.check(tools["write_file"], {"path": "x.txt"}, workspace)

    assert decision.allowed is True
    assert decision.reason == "approval_required"


def test_permission_mode_accept_edits_auto_file(tmp_path: Path) -> None:
    """accept_edits mode auto-approves write_file/patch_file."""
    workspace, tools = _tools(tmp_path)
    checker = PermissionChecker(permission_mode="accept_edits")

    decision = checker.check(tools["write_file"], {"path": "x.txt"}, workspace)

    assert decision.allowed is True
    assert decision.reason == "accept_edits_auto"


def test_permission_mode_accept_edits_ask_shell(tmp_path: Path) -> None:
    """accept_edits mode still requests approval for run_shell."""
    workspace, tools = _tools(tmp_path)
    checker = PermissionChecker(permission_mode="accept_edits")

    decision = checker.check(tools["run_shell"], {"command": "echo hi"}, workspace)

    assert decision.allowed is True
    assert decision.reason == "approval_required"


def test_permission_mode_auto_allows_all(tmp_path: Path) -> None:
    """auto mode auto-approves both write_file and run_shell."""
    workspace, tools = _tools(tmp_path)
    checker = PermissionChecker(permission_mode="auto")

    write_decision = checker.check(tools["write_file"], {"path": "x.txt"}, workspace)
    shell_decision = checker.check(tools["run_shell"], {"command": "echo hi"}, workspace)

    assert write_decision.allowed is True
    assert write_decision.reason == "approval_auto"
    assert shell_decision.allowed is True
    assert shell_decision.reason == "approval_auto"


def test_permission_mode_auto_keeps_dangerous_shell_behind_approval(tmp_path: Path) -> None:
    """Automatic mode does not bypass approval for destructive shell commands."""
    workspace, tools = _tools(tmp_path)
    checker = PermissionChecker(permission_mode="auto")

    decision = checker.check(
        tools["run_shell"],
        {"command": "git reset --hard HEAD"},
        workspace,
    )

    assert decision.allowed is True
    assert decision.reason == "approval_required"


def test_permission_mode_plan_blocks_writes(tmp_path: Path) -> None:
    """plan mode denies write_file with the plan write guard."""
    workspace, tools = _tools(tmp_path)
    checker = PermissionChecker(permission_mode="plan")

    decision = checker.check(tools["write_file"], {"path": "x.txt"}, workspace)

    assert decision.allowed is False
    assert decision.reason == "plan_mode_tool_not_allowed"
    assert decision.security_event_type == "plan_mode_write_guard"


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


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "find . -name '*.py'",
        "cat app.py",
        "rg alpha src | head -5",
        "grep -R alpha . && ls -la",
    ],
)
def test_policy_rejects_commands_whose_only_purpose_is_workspace_read(
    tmp_path: Path, command: str
) -> None:
    workspace, tools = _tools(tmp_path)

    decision = ToolPolicyChecker(workspace).check(
        tools["run_shell"], {"command": command}
    )

    assert decision.allowed is False
    assert decision.reason == "shell_search_should_use_tool"


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q | tail -5",
        "python3 --version; pwd; ls -la",
        "echo 'Hello'; pwd; ls -la",
        "npm run build && ls -la",
    ],
)
def test_policy_allows_compound_shell_validation_commands(
    tmp_path: Path, command: str
) -> None:
    workspace, tools = _tools(tmp_path)

    decision = ToolPolicyChecker(workspace).check(
        tools["run_shell"], {"command": command}
    )

    assert decision.allowed is True
