"""Server-owned sandbox provider selection for Harness runs."""

from __future__ import annotations

from typing import Literal

from sage_harness import SandboxPolicyError, SandboxPort

from core.coding.context import WorkspaceContext
from core.harness.local_sandbox import LocalWorkspaceSandbox

SandboxProviderName = Literal["local_workspace", "container"]


def create_coding_sandbox(
    workspace: WorkspaceContext,
    *,
    thread_id: str,
    app_env: str,
    provider: str = "local_workspace",
    allow_host_shell: bool = True,
    allow_writes: bool = True,
) -> SandboxPort:
    """Create the configured sandbox or fail closed before graph execution.

    ``local_workspace`` is intentionally limited to trusted local environments.
    ``container`` is a reserved provider until a real isolated implementation is
    registered; it must never silently fall back to host execution.
    """
    normalized = provider.strip().lower() or "local_workspace"
    if normalized == "local_workspace":
        return LocalWorkspaceSandbox(
            workspace,
            thread_id=thread_id,
            app_env=app_env,
            allow_host_shell=allow_host_shell,
            allow_writes=allow_writes,
        )
    if normalized == "container":
        raise SandboxPolicyError(
            "container sandbox provider is not installed; refusing host fallback"
        )
    raise SandboxPolicyError(f"unknown sandbox provider: {normalized}")


__all__ = ["SandboxProviderName", "create_coding_sandbox"]
