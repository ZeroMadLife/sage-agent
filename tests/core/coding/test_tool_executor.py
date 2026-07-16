"""ToolExecutor pipeline tests."""

from pathlib import Path

import pytest

from core.coding.context import WorkspaceContext
from core.coding.engine import (
    ApprovalGrantedEvent,
    ApprovalRequiredEvent,
    CancelledEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from core.coding.tool_executor import (
    ApprovalManager,
    PermissionChecker,
    ToolExecutor,
    ToolPolicyChecker,
)
from core.coding.tools.registry import build_tool_registry


def _executor(
    tmp_path: Path,
    *,
    approval_policy: str = "auto",
    approval_manager: ApprovalManager | None = None,
    session_id: str = "coding_1",
    should_stop: bool = False,
) -> ToolExecutor:
    workspace = WorkspaceContext(root=tmp_path)
    tools = build_tool_registry(workspace)
    # Map legacy approval_policy to permission_mode for backward compat in tests
    mode = "auto" if approval_policy == "auto" else "default"
    return ToolExecutor(
        tools=tools,
        workspace=workspace,
        permission_checker=PermissionChecker(permission_mode=mode, approval_policy=approval_policy),
        policy_checker=ToolPolicyChecker(workspace),
        approval_manager=approval_manager,
        session_id=session_id,
        should_stop=lambda: should_stop,
        run_id="run_1",
    )


async def test_unknown_tool_returns_error_event(tmp_path: Path) -> None:
    """Unknown tool names become tool_result errors."""
    executor = _executor(tmp_path)

    events = [event async for event in executor.execute({"name": "missing", "args": {}})]

    assert len(events) == 1
    assert isinstance(events[0], ToolResultEvent)
    assert events[0].is_error is True
    assert events[0].content == "unknown tool: missing"


async def test_permission_denied_returns_security_metadata(tmp_path: Path) -> None:
    """Raw permission denials include security metadata."""
    executor = _executor(tmp_path, approval_policy="never")

    events = [
        event
        async for event in executor.execute(
            {"name": "write_file", "args": {"path": "note.txt", "content": "x"}}
        )
    ]

    result = events[0]
    assert isinstance(result, ToolResultEvent)
    assert result.is_error is True
    assert result.security_event_type == "approval_denied"


async def test_policy_denied_returns_policy_reason(tmp_path: Path) -> None:
    """Policy denials include the policy reason for trace/debug UI."""
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    executor = _executor(tmp_path)

    events = [
        event
        async for event in executor.execute(
            {
                "name": "patch_file",
                "args": {
                    "path": "app.py",
                    "old_text": "value = 1",
                    "new_text": "value = 2",
                },
            }
        )
    ]

    result = events[0]
    assert isinstance(result, ToolResultEvent)
    assert result.is_error is True
    assert result.policy_reason == "prior_read_required"


async def test_auto_approval_path_executes_tool(tmp_path: Path) -> None:
    """Auto approval policy proceeds directly to tool_call/tool_result."""
    executor = _executor(tmp_path)

    events = [
        event
        async for event in executor.execute(
            {"name": "write_file", "args": {"path": "note.txt", "content": "approved"}}
        )
    ]

    assert [event.type for event in events] == ["tool_call", "tool_result"]
    assert isinstance(events[0], ToolCallEvent)
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "approved"


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("knowledge_learn", {"topic": "Harness lessons", "citation_ids": ["kcite_123"]}),
        ("remember", {"topic": "project-conventions", "fact": "Keep revisions bound"}),
    ],
)
async def test_durable_learning_requires_explicit_user_approval(
    tmp_path: Path,
    tool: str,
    args: dict[str, object],
) -> None:
    """Durable learning pauses before execution even when content is model-selected."""
    manager = ApprovalManager()
    executor = _executor(tmp_path, approval_manager=manager)
    stream = executor.execute(
        {
            "name": tool,
            "args": args,
        }
    )

    first = await anext(stream)

    assert isinstance(first, ApprovalRequiredEvent)
    assert first.tool == tool
    await stream.aclose()


async def test_invalid_write_arguments_fail_before_approval(tmp_path: Path) -> None:
    """An incomplete write request never reaches the user approval queue."""
    manager = ApprovalManager()
    executor = _executor(tmp_path, approval_policy="ask", approval_manager=manager)
    stream = executor.execute({"name": "write_file", "args": {"content": "missing path"}})

    first = await anext(stream)
    await stream.aclose()

    assert isinstance(first, ToolResultEvent)
    assert first.is_error is True
    assert "path" in first.content.lower()
    assert manager.pending("coding_1") is None


async def test_auto_mode_still_requires_approval_for_dangerous_shell(tmp_path: Path) -> None:
    """Dangerous shell commands keep an approval boundary in automatic mode."""
    manager = ApprovalManager()
    executor = _executor(tmp_path, approval_manager=manager)
    stream = executor.execute({"name": "run_shell", "args": {"command": "git reset --hard HEAD"}})

    first = await anext(stream)
    await stream.aclose()

    assert isinstance(first, ApprovalRequiredEvent)
    assert "reset" in first.description.lower()


async def test_ask_approval_granted_then_executes_tool(tmp_path: Path) -> None:
    """Ask approval mode blocks, resumes, and then executes after approval."""
    manager = ApprovalManager()
    executor = _executor(tmp_path, approval_policy="ask", approval_manager=manager)
    stream = executor.execute(
        {"name": "write_file", "args": {"path": "note.txt", "content": "approved"}}
    )

    first = await anext(stream)
    assert isinstance(first, ApprovalRequiredEvent)
    assert manager.resolve("coding_1", first.approval_id, "once") is True

    rest = [event async for event in stream]

    assert isinstance(rest[0], ApprovalGrantedEvent)
    assert [event.type for event in rest] == [
        "approval_granted",
        "tool_call",
        "tool_result",
    ]
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "approved"


async def test_session_approval_skips_the_next_matching_tool_prompt(tmp_path: Path) -> None:
    """A session approval is reused for the same risky tool within the session."""
    manager = ApprovalManager()
    executor = _executor(tmp_path, approval_policy="ask", approval_manager=manager)
    first_stream = executor.execute(
        {"name": "write_file", "args": {"path": "first.txt", "content": "first"}}
    )

    first = await anext(first_stream)
    assert isinstance(first, ApprovalRequiredEvent)
    assert manager.resolve("coding_1", first.approval_id, "session") is True
    _ = [event async for event in first_stream]

    second_stream = executor.execute(
        {"name": "write_file", "args": {"path": "second.txt", "content": "second"}}
    )
    events = [event async for event in second_stream]

    assert [event.type for event in events] == [
        "approval_granted",
        "tool_call",
        "tool_result",
    ]
    assert (tmp_path / "second.txt").read_text(encoding="utf-8") == "second"


async def test_ask_approval_denied_returns_error(tmp_path: Path) -> None:
    """Denied approvals become tool_result errors and do not execute."""
    manager = ApprovalManager()
    executor = _executor(tmp_path, approval_policy="ask", approval_manager=manager)
    stream = executor.execute(
        {"name": "write_file", "args": {"path": "note.txt", "content": "approved"}}
    )

    first = await anext(stream)
    assert isinstance(first, ApprovalRequiredEvent)
    assert manager.resolve("coding_1", first.approval_id, "deny") is True

    rest = [event async for event in stream]

    assert len(rest) == 1
    assert isinstance(rest[0], ToolResultEvent)
    assert rest[0].is_error is True
    assert not (tmp_path / "note.txt").exists()


async def test_stop_before_execution_returns_cancelled(tmp_path: Path) -> None:
    """A stop flag cancels before any tool logic runs."""
    executor = _executor(tmp_path, should_stop=True)

    events = [
        event
        async for event in executor.execute({"name": "read_file", "args": {"path": "README.md"}})
    ]

    assert len(events) == 1
    assert isinstance(events[0], CancelledEvent)


async def test_successful_read_file_event_order(tmp_path: Path) -> None:
    """Read-only tools emit tool_call before tool_result."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    executor = _executor(tmp_path)

    events = [
        event
        async for event in executor.execute({"name": "read_file", "args": {"path": "README.md"}})
    ]

    assert [event.type for event in events] == ["tool_call", "tool_result"]
    assert isinstance(events[1], ToolResultEvent)
    assert events[1].is_error is False
    assert "# Sage" in events[1].content
