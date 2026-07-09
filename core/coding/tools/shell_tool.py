"""Shell tool for the coding agent."""

from __future__ import annotations

import os
import subprocess
import textwrap
from typing import Any

from core.coding.context import WorkspaceContext, clip
from core.coding.tools.base import ToolContext, ToolResult
from core.coding.tools.registry import register_tool
from core.coding.tools.schemas import RunShellArgs

ALLOWED_SHELL_ENV = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PYTHONPATH",
    "CONDA_DEFAULT_ENV",
    "CONDA_PREFIX",
    "VIRTUAL_ENV",
}


@register_tool(
    name="run_shell",
    description="Run a shell command in the workspace root.",
    schema={"command": "str", "timeout": "int=20"},
    schema_model=RunShellArgs,
    risky=True,
    category="shell",
)
def run_shell(
    workspace: WorkspaceContext,
    args: dict[str, Any],
    tool_context: ToolContext | None = None,
) -> ToolResult:
    """Run a shell command with timeout and filtered environment."""
    _ = tool_context
    command = str(args["command"])
    timeout = int(args.get("timeout", 20))
    try:
        result = subprocess.run(
            command,
            cwd=workspace.root,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_filtered_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(content=f"command timed out after {timeout}s", is_error=True)
    content = textwrap.dedent(
        f"""\
        exit_code: {result.returncode}
        stdout:
        {result.stdout.strip() or "(empty)"}
        stderr:
        {result.stderr.strip() or "(empty)"}
        """
    ).strip()
    return ToolResult(content=clip(content), is_error=result.returncode != 0)


def _filtered_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in ALLOWED_SHELL_ENV}
