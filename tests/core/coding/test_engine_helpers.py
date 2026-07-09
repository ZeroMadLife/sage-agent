"""Coding engine helper tests."""

from core.coding.engine import (
    build_tool_descriptions,
    normalize_tool_payload,
    step_limit_summary,
)
from core.coding.tools.base import RegisteredTool, ToolResult


def test_normalize_tool_payload_defensively_handles_bad_args() -> None:
    """Tool payload normalization keeps malformed model output non-fatal."""
    name, args = normalize_tool_payload({"name": "read_file", "args": "README.md"})

    assert name == "read_file"
    assert args == {}


def test_build_tool_descriptions_contains_schema_and_description() -> None:
    """Tool descriptions expose the model-facing contract."""
    tools = {
        "read_file": RegisteredTool(
            name="read_file",
            schema={"path": "str"},
            description="Read a file.",
            risky=False,
            runner=lambda _args: ToolResult(content=""),
        )
    }

    descriptions = build_tool_descriptions(tools)

    assert descriptions == ["read_file: Read a file. schema={'path': 'str'}"]


def test_step_limit_summary_is_three_part_continuation_note() -> None:
    """Step-limit fallback tells the user what happened and how to continue."""
    summary = step_limit_summary("读 README", 3)

    assert "已完成:" in summary
    assert "未完成:" in summary
    assert "如何继续:" in summary
    assert "已执行 3 个工具步骤" in summary
