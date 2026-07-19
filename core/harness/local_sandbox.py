"""Trusted-development adapter from Sage coding tools to the Sandbox port."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping

from sage_harness import (
    SandboxCapabilities,
    SandboxDescriptor,
    SandboxOperation,
    SandboxPolicyError,
    SandboxResult,
)

from core.coding.context import WorkspaceContext
from core.coding.memory import workspace_id_from_path
from core.coding.tools.registry import execute_tool

_LOCAL_ENVIRONMENTS = frozenset({"development", "test"})
_READ_OPERATIONS = frozenset({"list_files", "read_file", "search"})
_WRITE_OPERATIONS = frozenset({"write_file", "patch_file"})
_ALL_OPERATIONS = _READ_OPERATIONS | _WRITE_OPERATIONS | {"run_shell"}


class LocalWorkspaceSandbox:
    """Expose one existing Coding workspace without claiming host isolation.

    This adapter deliberately reuses Sage's validated tool implementations.
    It is a lower-level capability backend: callers still have to pass through
    ToolExecutor policy and approval before invoking write or shell operations.
    """

    def __init__(
        self,
        workspace: WorkspaceContext,
        *,
        thread_id: str,
        app_env: str = "development",
        allow_host_shell: bool = False,
        allow_writes: bool = True,
    ) -> None:
        environment = app_env.strip().lower()
        if environment not in _LOCAL_ENVIRONMENTS:
            raise SandboxPolicyError(
                "LocalWorkspaceSandbox is disabled outside development/test; "
                "configure an isolated container sandbox instead"
            )
        normalized_thread = thread_id.strip()
        if not normalized_thread:
            raise ValueError("thread_id must not be empty")
        self._workspace = workspace
        self._closed = False
        workspace_id = workspace_id_from_path(workspace.root)
        thread_digest = hashlib.sha256(normalized_thread.encode()).hexdigest()[:12]
        self._descriptor = SandboxDescriptor(
            sandbox_id=f"local:{workspace_id}:{thread_digest}",
            provider="local_workspace",
            workspace_id=workspace_id,
            capabilities=SandboxCapabilities(
                isolated=False,
                host_access=True,
                read_files=True,
                write_files=allow_writes,
                shell=allow_host_shell,
            ),
        )

    @property
    def descriptor(self) -> SandboxDescriptor:
        return self._descriptor

    async def invoke(
        self,
        operation: SandboxOperation,
        arguments: Mapping[str, object],
    ) -> SandboxResult:
        """Execute one already-authorized operation through existing Sage tools."""
        if self._closed:
            raise SandboxPolicyError("sandbox is closed")
        if operation not in _ALL_OPERATIONS:
            raise SandboxPolicyError(f"unsupported sandbox operation: {operation}")
        capabilities = self._descriptor.capabilities
        if operation in _WRITE_OPERATIONS and not capabilities.write_files:
            raise SandboxPolicyError("sandbox file writes are disabled")
        if operation == "run_shell" and not capabilities.shell:
            raise SandboxPolicyError(
                "host shell is disabled for LocalWorkspaceSandbox; enable it only "
                "for a fully trusted local development session"
            )
        result = await asyncio.to_thread(
            execute_tool,
            self._workspace,
            operation,
            dict(arguments),
        )
        return SandboxResult(
            operation=operation,
            content=result.content,
            is_error=result.is_error,
            error_code=result.error_code,
            retryable=result.retryable,
            metadata={
                "sandbox_id": self._descriptor.sandbox_id,
                "workspace_id": self._descriptor.workspace_id,
                "provider": self._descriptor.provider,
            },
        )

    async def aclose(self) -> None:
        """Close this logical handle; the local adapter owns no subprocess pool."""
        self._closed = True


__all__ = ["LocalWorkspaceSandbox"]
