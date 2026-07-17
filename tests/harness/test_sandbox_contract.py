"""Contract tests for the application-neutral and local sandbox boundary."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sage_harness import SandboxCapabilities, SandboxPolicyError

from core.coding.context import WorkspaceContext
from core.harness.container_sandbox import ContainerWorkspaceSandbox
from core.harness.local_sandbox import LocalWorkspaceSandbox


def test_descriptor_is_scoped_without_exposing_the_host_workspace(tmp_path: Path) -> None:
    first = LocalWorkspaceSandbox(WorkspaceContext(tmp_path), thread_id="thread-a")
    second = LocalWorkspaceSandbox(WorkspaceContext(tmp_path), thread_id="thread-b")

    assert first.descriptor.sandbox_id != second.descriptor.sandbox_id
    assert first.descriptor.provider == "local_workspace"
    assert first.descriptor.capabilities == SandboxCapabilities(
        isolated=False,
        host_access=True,
        read_files=True,
        write_files=True,
        shell=False,
    )
    assert str(tmp_path) not in repr(first.descriptor)


def test_local_sandbox_reuses_workspace_path_validation_and_file_tools(tmp_path: Path) -> None:
    async def run() -> tuple[object, object, object]:
        workspace = WorkspaceContext(tmp_path)
        sandbox = LocalWorkspaceSandbox(workspace, thread_id="thread-a")
        wrote = await sandbox.invoke(
            "write_file",
            {"path": "notes/example.txt", "content": "alpha\nbeta\n"},
        )
        read = await sandbox.invoke(
            "read_file",
            {"path": "notes/example.txt", "start": 1, "end": 2},
        )
        searched = await sandbox.invoke(
            "search",
            {"path": "notes", "pattern": "beta"},
        )
        await sandbox.aclose()
        return wrote, read, searched

    wrote, read, searched = asyncio.run(run())

    assert wrote.is_error is False
    assert "notes/example.txt" in wrote.content
    assert "1: alpha" in read.content
    assert "notes/example.txt:2:beta" in searched.content


def test_local_sandbox_rejects_workspace_escape(tmp_path: Path) -> None:
    async def run() -> object:
        sandbox = LocalWorkspaceSandbox(WorkspaceContext(tmp_path), thread_id="thread-a")
        return await sandbox.invoke("read_file", {"path": "../secret.txt"})

    result = asyncio.run(run())

    assert result.is_error is True
    assert "escapes workspace root" in result.content


@pytest.mark.parametrize("app_env", ["production", "staging", "preview"])
def test_local_sandbox_is_disabled_outside_trusted_local_environments(
    tmp_path: Path,
    app_env: str,
) -> None:
    with pytest.raises(SandboxPolicyError, match="isolated container"):
        LocalWorkspaceSandbox(
            WorkspaceContext(tmp_path),
            thread_id="thread-a",
            app_env=app_env,
        )


def test_host_shell_and_file_writes_are_independent_capabilities(tmp_path: Path) -> None:
    async def run() -> tuple[object, object]:
        readonly = LocalWorkspaceSandbox(
            WorkspaceContext(tmp_path),
            thread_id="thread-readonly",
            allow_writes=False,
        )
        with pytest.raises(SandboxPolicyError, match="writes are disabled"):
            await readonly.invoke("write_file", {"path": "blocked.txt", "content": "x"})
        with pytest.raises(SandboxPolicyError, match="host shell is disabled"):
            await readonly.invoke("run_shell", {"command": "printf blocked"})

        shell = LocalWorkspaceSandbox(
            WorkspaceContext(tmp_path),
            thread_id="thread-shell",
            allow_host_shell=True,
            allow_writes=False,
        )
        result = await shell.invoke("run_shell", {"command": "printf sandbox-ok"})
        await shell.aclose()
        return readonly.descriptor, result

    descriptor, result = asyncio.run(run())

    assert descriptor.capabilities.write_files is False
    assert descriptor.capabilities.shell is False
    assert result.is_error is False
    assert "sandbox-ok" in result.content


def test_closed_local_sandbox_rejects_new_operations(tmp_path: Path) -> None:
    async def run() -> None:
        sandbox = LocalWorkspaceSandbox(WorkspaceContext(tmp_path), thread_id="thread-a")
        await sandbox.aclose()
        with pytest.raises(SandboxPolicyError, match="closed"):
            await sandbox.invoke("list_files", {"path": "."})

    asyncio.run(run())


def test_isolated_capability_cannot_also_claim_host_access() -> None:
    with pytest.raises(ValueError, match="isolated sandbox"):
        SandboxCapabilities(isolated=True, host_access=True)


def test_container_health_reports_missing_without_leaking_host_path(tmp_path: Path) -> None:
    async def run() -> object:
        sandbox = ContainerWorkspaceSandbox(
            WorkspaceContext(tmp_path),
            thread_id="health-missing",
            docker_binary="sage-docker-does-not-exist",
        )
        return await sandbox.health()

    health = asyncio.run(run())

    assert health["status"] == "unavailable"
    assert health["healthy"] is False
    assert str(tmp_path) not in repr(health)
