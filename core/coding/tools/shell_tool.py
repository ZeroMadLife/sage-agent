"""Shell tool for the coding agent."""

from __future__ import annotations

import os
import signal
import subprocess
import textwrap
from contextlib import suppress
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
    timeout=130.0,
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
    process = subprocess.Popen(
        command,
        cwd=workspace.root,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_filtered_env(),
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        stdout, stderr = _terminate_process_group(process)
        content = textwrap.dedent(
            f"""\
            command timed out after {timeout}s
            failure_kind: timeout
            retryable: true
            timeout_seconds: {timeout}
            stdout:
            {stdout.strip() or "(empty)"}
            stderr:
            {stderr.strip() or "(empty)"}
            """
        ).strip()
        return ToolResult(
            content=clip(content),
            is_error=True,
            error_code="shell_timeout",
            retryable=True,
        )
    content = textwrap.dedent(
        f"""\
        exit_code: {process.returncode}
        stdout:
        {stdout.strip() or "(empty)"}
        stderr:
        {stderr.strip() or "(empty)"}
        """
    ).strip()
    return ToolResult(
        content=clip(content),
        is_error=process.returncode != 0,
        error_code="shell_exit_nonzero" if process.returncode != 0 else None,
        retryable=False if process.returncode != 0 else None,
    )


def _terminate_process_group(
    process: subprocess.Popen[str],
) -> tuple[str, str]:
    """Terminate the shell and every descendant before returning a timeout."""
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        return process.communicate(timeout=0.5)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        return process.communicate()


def _filtered_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if key in ALLOWED_SHELL_ENV}
