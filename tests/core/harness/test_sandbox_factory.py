"""Server-owned sandbox provider selection tests."""

from pathlib import Path

import pytest
from sage_harness import SandboxPolicyError

from core.coding.context import WorkspaceContext
from core.harness.container_sandbox import ContainerWorkspaceSandbox
from core.harness.sandbox_factory import (
    create_coding_sandbox,
    normalize_sandbox_provider,
    reconcile_coding_sandboxes,
)


def test_local_provider_is_selected_for_trusted_development(tmp_path: Path) -> None:
    sandbox = create_coding_sandbox(
        WorkspaceContext(tmp_path),
        thread_id="thread-local",
        app_env="development",
    )

    assert sandbox.descriptor.provider == "local_workspace"


def test_container_provider_selects_isolated_adapter(tmp_path: Path) -> None:
    sandbox = create_coding_sandbox(
        WorkspaceContext(tmp_path),
        thread_id="thread-container",
        app_env="production",
        provider="container",
    )

    assert isinstance(sandbox, ContainerWorkspaceSandbox)
    assert sandbox.descriptor.provider == "container"
    assert sandbox.descriptor.capabilities.isolated is True
    assert sandbox.descriptor.capabilities.host_access is False


def test_container_provider_does_not_fallback_when_docker_is_missing(tmp_path: Path) -> None:
    sandbox = ContainerWorkspaceSandbox(
        WorkspaceContext(tmp_path),
        thread_id="thread-container-missing",
        docker_binary="sage-docker-does-not-exist",
    )

    with pytest.raises(SandboxPolicyError, match="docker executable"):
        import asyncio

        asyncio.run(sandbox.invoke("list_files", {"path": "."}))


def test_container_provider_projects_invalid_workspace_paths_as_tool_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    sandbox = ContainerWorkspaceSandbox(
        WorkspaceContext(tmp_path),
        thread_id="thread-container-policy",
    )

    def reject_path(operation: object, arguments: object) -> tuple[str, bool]:
        _ = operation, arguments
        raise ValueError("path is protected by workspace policy: .sage/usage.sqlite3")

    monkeypatch.setattr(sandbox, "_ensure_started", lambda: None)
    monkeypatch.setattr(sandbox, "_invoke_sync", reject_path)

    result = asyncio.run(sandbox.invoke("read_file", {"path": ".sage/usage.sqlite3"}))

    assert result.is_error is True
    assert "protected by workspace policy" in result.content


def test_unknown_provider_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(SandboxPolicyError, match="unknown sandbox provider"):
        create_coding_sandbox(
            WorkspaceContext(tmp_path),
            thread_id="thread-unknown",
            app_env="development",
            provider="host",
        )


def test_provider_normalization_is_strict() -> None:
    assert normalize_sandbox_provider(" CONTAINER ") == "container"
    with pytest.raises(SandboxPolicyError, match="unknown sandbox provider"):
        normalize_sandbox_provider("host")


def test_reconcile_is_noop_for_local_provider() -> None:
    assert reconcile_coding_sandboxes("local_workspace") == 0


def test_reconcile_fails_closed_when_docker_is_missing() -> None:
    with pytest.raises(SandboxPolicyError, match="docker executable"):
        reconcile_coding_sandboxes("container", docker_binary="sage-docker-does-not-exist")
