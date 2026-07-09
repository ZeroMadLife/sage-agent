"""Helper functions for the coding engine loop."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.coding.tools.base import RegisteredTool


def normalize_tool_payload(payload: Any) -> tuple[str, dict[str, Any]]:
    """Convert parsed model output into a safe tool name/args pair."""
    if not isinstance(payload, dict):
        return "", {}
    name = str(payload.get("name", ""))
    args = payload.get("args", {})
    return name, args if isinstance(args, dict) else {}


def build_tool_descriptions(tools: Mapping[str, RegisteredTool]) -> list[str]:
    """Build model-facing tool descriptions."""
    return [f"{tool.name}: {tool.description} schema={tool.schema}" for tool in tools.values()]


def step_limit_summary(user_message: str, tool_steps: int) -> str:
    """Render the fallback response when the model never emits final."""
    return "\n".join(
        [
            "已完成:",
            f"- 已执行 {tool_steps} 个工具步骤来处理：{user_message}",
            "未完成:",
            "- 模型在步数限制内没有给出 <final>。",
            "如何继续:",
            "- 请继续本任务，或缩小请求范围后重试。",
        ]
    )
