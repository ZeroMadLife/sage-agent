"""Server-owned sandbox provider selection for Harness runs."""

from __future__ import annotations

from typing import Literal, cast

from sage_harness import SandboxPolicyError, SandboxPort

from core.coding.context import WorkspaceContext
from core.harness.container_sandbox import ContainerWorkspaceSandbox
from core.harness.local_sandbox import LocalWorkspaceSandbox
from core.harness.native_local_sandbox import NativeLocalSandbox

SandboxProviderName = Literal["local_workspace", "native_local", "container"]
SANDBOX_PROVIDERS = frozenset({"local_workspace", "native_local", "container"})
DISPOSABLE_SANDBOX_PROVIDERS = frozenset({"native_local", "container"})


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
    workspace_id: str | None = None,
    logical_workspace_root: str | None = None,
) -> SandboxPort:
    """创建配置的 Sandbox，并在图执行前对不安全配置 fail closed。

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
            workspace_id=workspace_id,
        )
    if normalized == "native_local":
        return NativeLocalSandbox(
            workspace,
            thread_id=thread_id,
            app_env=app_env,
            allow_host_shell=allow_host_shell,
            allow_writes=allow_writes,
            workspace_id=workspace_id,
            logical_workspace_root=logical_workspace_root,
        )
    if normalized == "container":
        try:
            return ContainerWorkspaceSandbox(
                workspace,
                thread_id=thread_id,
                image=container_image,
                allow_host_shell=allow_host_shell,
                allow_writes=allow_writes,
                workspace_id=workspace_id,
            )
        except (ValueError, SandboxPolicyError):
            raise
        except Exception as exc:
            raise SandboxPolicyError("container sandbox provider failed to initialize") from exc
    raise AssertionError(f"unhandled sandbox provider: {normalized}")


__all__ = [
    "DISPOSABLE_SANDBOX_PROVIDERS",
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


def discard_coding_sandbox(
    provider: str,
    workspace: WorkspaceContext,
    *,
    thread_id: str,
    workspace_id: str,
    container_image: str = "python:3.11-slim",
    docker_binary: str = "docker",
) -> int:
    """在 worktree 删除前释放对应容器；无法确认归属时保持 fail closed。"""
    normalized = normalize_sandbox_provider(provider)
    if normalized in {"local_workspace", "native_local"}:
        return 0
    return ContainerWorkspaceSandbox.discard_owned(
        workspace,
        thread_id=thread_id,
        workspace_id=workspace_id,
        image=container_image,
        docker_binary=docker_binary,
    )


__all__.append("reconcile_coding_sandboxes")
__all__.append("discard_coding_sandbox")
