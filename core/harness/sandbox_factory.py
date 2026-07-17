"""Server-owned sandbox provider selection for Harness runs."""

from __future__ import annotations

from typing import Literal, cast

from sage_harness import SandboxPolicyError, SandboxPort

from core.coding.context import WorkspaceContext
from core.harness.container_sandbox import ContainerWorkspaceSandbox
from core.harness.local_sandbox import LocalWorkspaceSandbox

SandboxProviderName = Literal["local_workspace", "container"]
SANDBOX_PROVIDERS = frozenset({"local_workspace", "container"})


def normalize_sandbox_provider(value: object) -> SandboxProviderName:
    """Normalize a deployment provider without accepting unsafe fallbacks."""
    normalized = str(value).strip().lower() or "local_workspace"
    if normalized not in SANDBOX_PROVIDERS:
        raise SandboxPolicyError(f"unknown sandbox provider: {normalized}")
    return cast(SandboxProviderName, normalized)


def create_coding_sandbox(
    workspace: WorkspaceContext,
    *,
    thread_id: str,
    app_env: str,
    provider: str = "local_workspace",
    allow_host_shell: bool = True,
    allow_writes: bool = True,
    container_image: str = "python:3.11-slim",
) -> SandboxPort:
    """Create the configured sandbox or fail closed before graph execution.

    ``local_workspace`` is intentionally limited to trusted local environments.
    ``container`` uses the server-owned Docker adapter and must never silently
    fall back to host execution.
    """
    normalized = normalize_sandbox_provider(provider)
    if normalized == "local_workspace":
        return LocalWorkspaceSandbox(
            workspace,
            thread_id=thread_id,
            app_env=app_env,
            allow_host_shell=allow_host_shell,
            allow_writes=allow_writes,
        )
    if normalized == "container":
        try:
            return ContainerWorkspaceSandbox(
                workspace,
                thread_id=thread_id,
                image=container_image,
                allow_host_shell=allow_host_shell,
                allow_writes=allow_writes,
            )
        except (ValueError, SandboxPolicyError):
            raise
        except Exception as exc:
            raise SandboxPolicyError("container sandbox provider failed to initialize") from exc
    raise AssertionError(f"unhandled sandbox provider: {normalized}")


__all__ = [
    "SANDBOX_PROVIDERS",
    "SandboxProviderName",
    "create_coding_sandbox",
    "normalize_sandbox_provider",
]


def reconcile_coding_sandboxes(provider: str, *, docker_binary: str = "docker") -> int:
    """Reconcile terminal containers for the configured provider."""
    if provider.strip().lower() != "container":
        return 0
    return ContainerWorkspaceSandbox.reconcile_stopped(docker_binary=docker_binary)


__all__.append("reconcile_coding_sandboxes")
