"""End-to-end coding-agent loop tests with a scripted model client."""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.core.coding.scripted_api_client import ScriptedApiClient

from core.coding.context import ContextManager, WorkspaceContext
from core.coding.engine import Engine
from core.coding.tool_executor import (
    ApprovalManager,
    PermissionChecker,
    ToolPolicyChecker,
)
from core.coding.tools.registry import build_tool_registry


def _engine(
    tmp_path: Path,
    responses: list[str],
    *,
    approval_policy: str = "auto",
    approval_manager: ApprovalManager | None = None,
    max_steps: int = 5,
) -> Engine:
    workspace = WorkspaceContext(root=tmp_path)
    tools = build_tool_registry(workspace)
    mode = "auto" if approval_policy == "auto" else "default"
    return Engine(
        model=ScriptedApiClient(responses),
        workspace=workspace,
        tools=tools,
        context_manager=ContextManager(),
        permission_checker=PermissionChecker(permission_mode=mode, approval_policy=approval_policy),
        policy_checker=ToolPolicyChecker(workspace),
        session_id="coding_1",
        approval_manager=approval_manager,
        max_steps=max_steps,
    )


async def test_agent_loop_user_to_tool_to_final(tmp_path: Path) -> None:
    """User request can drive model -> tool -> model -> final without real API."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    engine = _engine(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
            "<final>README says Sage.</final>",
        ],
    )

    events = [event async for event in engine.run_turn("read README")]

    # Streaming emits text_delta chunks between model_requested and model_parsed;
    # collapse them to compare against the structural event sequence.
    structural = [event["type"] for event in events if event["type"] != "text_delta"]
    assert structural == [
        "model_requested",
        "model_parsed",
        "tool_call",
        "tool_result",
        "model_requested",
        "model_parsed",
        "final",
    ]
    assert events[-1]["content"] == "README says Sage."
    assert not [
        event for event in events if event["type"] == "text_delta" and "<tool" in event["delta"]
    ]
    assert "".join(event["delta"] for event in events if event["type"] == "text_delta") == (
        "README says Sage."
    )


async def test_agent_loop_user_to_final(tmp_path: Path) -> None:
    """A direct final response exits the loop without tool execution."""
    engine = _engine(tmp_path, ["<final>hello</final>"])

    events = [event async for event in engine.run_turn("say hello")]

    assert [event["type"] for event in events if event["type"] != "text_delta"] == [
        "model_requested",
        "model_parsed",
        "final",
    ]
    assert events[-1]["content"] == "hello"


async def test_agent_loop_keeps_protocol_corrections_out_of_session_history(tmp_path: Path) -> None:
    """Malformed output is corrected transiently instead of polluting the chat history."""
    engine = _engine(
        tmp_path,
        [
            "I will read the file now.",
            "<final>Recovered after the format correction.</final>",
        ],
    )

    events = [event async for event in engine.run_turn("read README")]

    assert [event["type"] for event in events if event["type"] == "retry"] == ["retry"]
    assert events[-1]["type"] == "final"
    assert all(
        "previous response could not be executed" not in str(item.get("content", "")).lower()
        for item in engine.history
    )


async def test_agent_loop_accepts_plain_final_after_tool_protocol_recovery(tmp_path: Path) -> None:
    """A provider that drops only the final tag can still finish a tool turn."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    engine = _engine(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
            "README says Sage.",
            "README says Sage.",
        ],
    )

    events = [event async for event in engine.run_turn("read README")]

    assert [event["type"] for event in events if event["type"] == "retry"] == ["retry"]
    assert events[-1]["type"] == "final"
    assert events[-1]["content"] == "README says Sage."
    assert engine.history[-1]["content"] == "README says Sage."


async def test_agent_loop_stops_after_bounded_protocol_retries(tmp_path: Path) -> None:
    """Repeated malformed model output ends in a clear final response, not a long retry loop."""

    class InvalidProtocolClient:
        async def complete(self, _prompt: str) -> str:
            return "I will keep using plain prose."

    workspace = WorkspaceContext(root=tmp_path)
    engine = Engine(
        model=InvalidProtocolClient(),
        workspace=workspace,
        tools=build_tool_registry(workspace),
        context_manager=ContextManager(),
        permission_checker=PermissionChecker(permission_mode="auto", approval_policy="auto"),
        policy_checker=ToolPolicyChecker(workspace),
        max_steps=5,
    )

    events = [event async for event in engine.run_turn("read README")]

    assert len([event for event in events if event["type"] == "retry"]) == 2
    assert len([event for event in events if event["type"] == "model_requested"]) == 3
    assert events[-1]["type"] == "final"
    assert "无法执行的操作格式" in events[-1]["content"]
    assert all(
        "previous response could not be executed" not in str(item.get("content", "")).lower()
        for item in engine.history
    )


async def test_agent_loop_policy_denied_then_final(tmp_path: Path) -> None:
    """Policy-denied tools are fed back to the model before final."""
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    engine = _engine(
        tmp_path,
        [
            (
                '<tool>{"name":"patch_file","args":{"path":"app.py",'
                '"old_text":"value = 1","new_text":"value = 2"}}</tool>'
            ),
            "<final>I need to read the file first.</final>",
        ],
    )

    events = [event async for event in engine.run_turn("change app")]

    assert [event["type"] for event in events if event["type"] != "text_delta"] == [
        "model_requested",
        "model_parsed",
        "tool_result",
        "model_requested",
        "model_parsed",
        "final",
    ]
    policy_error = next(event for event in events if event["type"] == "tool_result")
    assert policy_error["is_error"] is True
    assert policy_error["policy_reason"] == "prior_read_required"


async def test_agent_loop_approval_then_tool_then_final(tmp_path: Path) -> None:
    """Approval mode can pause the loop, resume the tool, and then finish."""
    manager = ApprovalManager()
    engine = _engine(
        tmp_path,
        [
            '<tool>{"name":"write_file","args":{"path":"note.txt","content":"ok"}}</tool>',
            "<final>written</final>",
        ],
        approval_policy="ask",
        approval_manager=manager,
    )

    async def collect() -> list[dict[str, object]]:
        return [event async for event in engine.run_turn("write note")]

    task = asyncio.create_task(collect())
    approval_id = ""
    for _ in range(50):
        pending = manager.pending("coding_1")
        if pending is not None:
            approval_id = str(pending["approval_id"])
            break
        await asyncio.sleep(0.01)
    assert approval_id
    assert manager.resolve("coding_1", approval_id, "once") is True

    events = await task

    assert [event["type"] for event in events if event["type"] != "text_delta"] == [
        "model_requested",
        "model_parsed",
        "approval_required",
        "approval_granted",
        "tool_call",
        "tool_result",
        "model_requested",
        "model_parsed",
        "final",
    ]
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "ok"


async def test_agent_loop_model_never_finishes_hits_step_limit(tmp_path: Path) -> None:
    """Repeated tool calls eventually emit step_limit."""
    (tmp_path / "README.md").write_text("# Sage\n", encoding="utf-8")
    engine = _engine(
        tmp_path,
        ['<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>'] * 3,
        max_steps=2,
    )

    events = [event async for event in engine.run_turn("loop")]

    assert events[-1]["type"] == "step_limit"
