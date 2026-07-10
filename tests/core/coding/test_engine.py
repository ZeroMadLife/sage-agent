"""Coding engine loop tests."""

from pathlib import Path

from tests.core.coding.scripted_api_client import ScriptedApiClient

from core.coding.context import SYSTEM_PROMPT_DYNAMIC_BOUNDARY, ContextManager, WorkspaceContext
from core.coding.engine import Engine
from core.coding.tool_executor import PermissionChecker, ToolPolicyChecker
from core.coding.tools.registry import build_tool_registry


class FakeModel:
    """Deterministic async model for engine tests."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses.pop(0)


class FakeAinvokeModel:
    """Async model exposing ainvoke; records the messages it receives."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    async def ainvoke(self, messages: list[dict[str, str]]) -> object:
        self.calls.append([dict(msg) for msg in messages])
        return self.responses.pop(0)


def _engine(tmp_path: Path, responses: list[str], max_steps: int = 5) -> Engine:
    workspace = WorkspaceContext(root=tmp_path)
    tools = build_tool_registry(workspace)
    return Engine(
        model=FakeModel(responses),
        workspace=workspace,
        tools=tools,
        context_manager=ContextManager(),
        permission_checker=PermissionChecker(permission_mode="auto", approval_policy="auto"),
        policy_checker=ToolPolicyChecker(workspace),
        max_steps=max_steps,
    )


async def test_engine_yields_tool_result_then_final(tmp_path: Path) -> None:
    """Engine runs model -> tool -> final and yields streamable events."""
    (tmp_path / "README.md").write_text("TourSwarm coding agent\n", encoding="utf-8")
    engine = _engine(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
            "<final>项目叫 TourSwarm coding agent。</final>",
        ],
    )

    events = [event async for event in engine.run_turn("读 README.md 告诉我项目叫什么")]

    assert [event["type"] for event in events] == [
        "model_requested",
        "model_parsed",
        "tool_call",
        "tool_result",
        "model_requested",
        "model_parsed",
        "final",
    ]
    assert events[2]["tool"] == "read_file"
    assert events[3]["is_error"] is False
    assert "TourSwarm" in events[3]["content"]
    assert events[-1]["content"] == "项目叫 TourSwarm coding agent。"


async def test_engine_denies_policy_violation_as_tool_result(tmp_path: Path) -> None:
    """Engine turns policy denials into visible tool_result errors."""
    (tmp_path / "app.py").write_text("value = 1\n", encoding="utf-8")
    engine = _engine(
        tmp_path,
        [
            (
                '<tool>{"name":"patch_file","args":{"path":"app.py",'
                '"old_text":"value = 1","new_text":"value = 2"}}</tool>'
            ),
            "<final>无法盲改。</final>",
        ],
    )

    events = [event async for event in engine.run_turn("把 value 改成 2")]

    policy_errors = [
        event
        for event in events
        if event["type"] == "tool_result" and event.get("is_error") is True
    ]
    assert policy_errors
    assert "fresh read_file" in policy_errors[0]["content"]


async def test_engine_emits_step_limit_when_model_never_finishes(tmp_path: Path) -> None:
    """Engine emits a step_limit event when model keeps asking for tools."""
    (tmp_path / "README.md").write_text("TourSwarm\n", encoding="utf-8")
    engine = _engine(
        tmp_path,
        ['<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>'] * 3,
        max_steps=2,
    )

    events = [event async for event in engine.run_turn("循环读文件")]

    assert events[-1]["type"] == "step_limit"
    assert "已完成" in events[-1]["content"]


async def test_engine_cancels_before_model_request(tmp_path: Path) -> None:
    """A stop request cancels the turn before another model call is started."""
    workspace = WorkspaceContext(root=tmp_path)
    tools = build_tool_registry(workspace)
    model = FakeModel(["<final>should not be called</final>"])
    engine = Engine(
        model=model,
        workspace=workspace,
        tools=tools,
        context_manager=ContextManager(),
        permission_checker=PermissionChecker(permission_mode="auto", approval_policy="auto"),
        policy_checker=ToolPolicyChecker(workspace),
        should_stop=lambda: True,
    )

    events = [event async for event in engine.run_turn("读 README")]

    assert len(events) == 1
    assert events[0]["type"] == "cancelled"
    assert events[0]["content"] == "已停止当前运行。"
    assert model.prompts == []


async def test_engine_tool_search_activates_deferred_tools_for_next_prompt(
    tmp_path: Path,
) -> None:
    """tool_search activation makes matching deferred tools visible on the next model turn."""
    workspace = WorkspaceContext(root=tmp_path)
    activated_tools: set[str] = set()
    tools = build_tool_registry(workspace, activated_tools=activated_tools)
    model = FakeModel(
        [
            '<tool>{"name":"tool_search","args":{"query":"todo"}}</tool>',
            "<final>todo tools ready</final>",
        ]
    )
    engine = Engine(
        model=model,
        workspace=workspace,
        tools=tools,
        context_manager=ContextManager(),
        permission_checker=PermissionChecker(permission_mode="auto", approval_policy="auto"),
        policy_checker=ToolPolicyChecker(workspace),
        activated_tools=activated_tools,
    )

    events = [event async for event in engine.run_turn("我要拆任务")]

    assert [event["type"] for event in events] == [
        "model_requested",
        "model_parsed",
        "tool_call",
        "tool_result",
        "model_requested",
        "model_parsed",
        "final",
    ]
    assert "Deferred tools" in model.prompts[0]
    assert "todo_add" in model.prompts[0]
    assert "todo_add:" not in model.prompts[0]
    assert "todo_add:" in model.prompts[1]
    assert "todo_add" in activated_tools


async def test_engine_ainvoke_splits_system_and_user_messages(tmp_path: Path) -> None:
    """The ainvoke branch sends the pre-boundary prompt as a system message."""
    (tmp_path / "README.md").write_text("TourSwarm coding agent\n", encoding="utf-8")
    workspace = WorkspaceContext(root=tmp_path)
    tools = build_tool_registry(workspace)
    model = FakeAinvokeModel(
        [
            '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>',
            "<final>项目叫 TourSwarm coding agent。</final>",
        ]
    )
    engine = Engine(
        model=model,
        workspace=workspace,
        tools=tools,
        context_manager=ContextManager(),
        permission_checker=PermissionChecker(permission_mode="auto", approval_policy="auto"),
        policy_checker=ToolPolicyChecker(workspace),
        max_steps=5,
    )

    events = [event async for event in engine.run_turn("读 README.md 告诉我项目叫什么")]

    assert events[-1]["type"] == "final"
    # Each model turn is one ainvoke call with a system + user pair.
    assert len(model.calls) == 2
    for messages in model.calls:
        assert [msg["role"] for msg in messages] == ["system", "user"]
        system_content = messages[0]["content"]
        user_content = messages[1]["content"]
        assert SYSTEM_PROMPT_DYNAMIC_BOUNDARY not in system_content
        assert SYSTEM_PROMPT_DYNAMIC_BOUNDARY not in user_content
        assert "You are Sage" in system_content
        assert "Current user request" in user_content


def test_build_ainvoke_messages_falls_back_to_single_user_message() -> None:
    """Without a boundary marker the whole prompt stays a single user message."""
    messages = Engine._build_ainvoke_messages("plain prompt without boundary")
    assert messages == [{"role": "user", "content": "plain prompt without boundary"}]


async def test_engine_streams_text_delta(tmp_path: Path) -> None:
    """run_turn streams only user-facing final text, never XML protocol tags."""
    final_text = "README says Sage."
    full_response = f"<final>{final_text}</final>"
    workspace = WorkspaceContext(root=tmp_path)
    tools = build_tool_registry(workspace)
    model = ScriptedApiClient([full_response])
    engine = Engine(
        model=model,
        workspace=workspace,
        tools=tools,
        context_manager=ContextManager(),
        permission_checker=PermissionChecker(permission_mode="auto", approval_policy="auto"),
        policy_checker=ToolPolicyChecker(workspace),
        max_steps=5,
    )

    events = [event async for event in engine.run_turn("read README")]

    delta_events = [event for event in events if event["type"] == "text_delta"]
    assert delta_events, "expected at least one text_delta event from the stream"
    reassembled = "".join(event["delta"] for event in delta_events)
    assert reassembled == final_text
    assert "<final>" not in reassembled

    # text_delta chunks must arrive before the matching model_parsed event and
    # must never appear after a final/terminal event.
    seen_parsed = False
    for event in events:
        if event["type"] == "model_parsed":
            seen_parsed = True
        if event["type"] == "text_delta":
            assert not seen_parsed, "text_delta arrived after model_parsed"
    assert events[-1]["type"] == "final"
    assert events[-1]["content"] == final_text

    # The streaming path consumes the scripted response through astream.
    assert model.calls, "astream should have been invoked with messages"
    assert model.prompts == [], "complete fallback should not run when astream exists"


async def test_engine_ainvoke_fallback_emits_no_text_delta(tmp_path: Path) -> None:
    """A model with ainvoke but no astream keeps the non-streaming path."""
    (tmp_path / "README.md").write_text("TourSwarm coding agent\n", encoding="utf-8")
    workspace = WorkspaceContext(root=tmp_path)
    tools = build_tool_registry(workspace)
    model = FakeAinvokeModel(
        ['<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>', "<final>done</final>"]
    )
    engine = Engine(
        model=model,
        workspace=workspace,
        tools=tools,
        context_manager=ContextManager(),
        permission_checker=PermissionChecker(permission_mode="auto", approval_policy="auto"),
        policy_checker=ToolPolicyChecker(workspace),
        max_steps=5,
    )

    events = [event async for event in engine.run_turn("read README")]

    assert not any(event["type"] == "text_delta" for event in events)
    assert events[-1]["type"] == "final"


async def test_engine_detects_repeated_tool_calls(tmp_path: Path) -> None:
    """Engine stops with a final event when a tool call repeats identically too often."""
    (tmp_path / "README.md").write_text("TourSwarm\n", encoding="utf-8")
    workspace = WorkspaceContext(root=tmp_path)
    tools = build_tool_registry(workspace)
    # The model keeps returning the exact same read_file tool call forever.
    repeated_call = '<tool>{"name":"read_file","args":{"path":"README.md"}}</tool>'
    model = ScriptedApiClient([repeated_call] * 8)
    engine = Engine(
        model=model,
        workspace=workspace,
        tools=tools,
        context_manager=ContextManager(),
        permission_checker=PermissionChecker(permission_mode="auto", approval_policy="auto"),
        policy_checker=ToolPolicyChecker(workspace),
        max_steps=50,
    )

    events = [event async for event in engine.run_turn("循环读文件")]

    # The loop guard terminates with a final event, never reaching the step limit.
    assert events[-1]["type"] == "final"
    assert events[-1]["type"] != "step_limit"
    assert "重复调用" in events[-1]["content"]
    assert "read_file" in events[-1]["content"]

    # The identical tool was allowed to run three times (repeat_count 0,1,2) before
    # the fourth repetition tripped the guard and emitted the final event.
    tool_results = [event for event in events if event["type"] == "tool_result"]
    assert len(tool_results) == 3


async def test_engine_detects_repeated_write_same_path_different_content(tmp_path: Path) -> None:
    """Loop guard catches repeated writes to the same path even if content differs."""
    workspace = WorkspaceContext(root=tmp_path)
    tools = build_tool_registry(workspace)
    # Model writes to the same path but varies content on each call.
    calls = [
        f'<tool>{{"name":"write_file","args":{{"path":"test.txt","content":"attempt {i}"}}}}</tool>'
        for i in range(8)
    ]
    model = ScriptedApiClient(calls)
    # Must read file first for policy, then auto-approve writes.
    (tmp_path / "test.txt").write_text("original", encoding="utf-8")
    # Simulate a prior read by writing a read entry into history.
    engine = Engine(
        model=model,
        workspace=workspace,
        tools=tools,
        context_manager=ContextManager(),
        permission_checker=PermissionChecker(permission_mode="auto"),
        policy_checker=ToolPolicyChecker(workspace),
        max_steps=50,
    )
    # Patch the policy checker to skip prior-read requirement for this test.
    engine.policy_checker.check = lambda tool, args: type(
        "P", (), {"allowed": True, "reason": "", "message": ""}
    )()

    events = [event async for event in engine.run_turn("循环写文件")]

    assert events[-1]["type"] == "final"
    assert "重复调用" in events[-1]["content"]
    assert "write_file" in events[-1]["content"]
