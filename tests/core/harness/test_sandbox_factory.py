"""Server-owned sandbox provider selection tests."""

from pathlib import Path

import pytest
from sage_harness import SandboxPolicyError

from core.coding.context import WorkspaceContext
from core.harness.sandbox_factory import create_coding_sandbox


def test_local_provider_is_selected_for_trusted_development(tmp_path: Path) -> None:
    sandbox = create_coding_sandbox(
        WorkspaceContext(tmp_path),
        thread_id="thread-local",
        app_env="development",
    )

    assert sandbox.descriptor.provider == "local_workspace"


def test_container_provider_never_falls_back_to_host(tmp_path: Path) -> None:
    with pytest.raises(SandboxPolicyError, match="refusing host fallback"):
        create_coding_sandbox(
            WorkspaceContext(tmp_path),
            thread_id="thread-container",
            app_env="production",
            provider="container",
        )


def test_unknown_provider_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(SandboxPolicyError, match="unknown sandbox provider"):
        create_coding_sandbox(
            WorkspaceContext(tmp_path),
            thread_id="thread-unknown",
            app_env="development",
            provider="host",
        )
