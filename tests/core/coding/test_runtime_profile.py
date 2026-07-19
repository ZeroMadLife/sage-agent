"""CodingRuntime persistence contract for Harness migration profiles."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.coding.persistence import CodingSessionStore
from core.coding.runtime import CodingRuntime


def test_runtime_profile_is_persisted_and_read_only(tmp_path: Path) -> None:
    runtime = CodingRuntime(
        session_id="profile-session",
        workspace_root=tmp_path,
        model=object(),
        storage_root=tmp_path / ".coding",
        runtime_profile="deerflow_v2",
    )

    persisted = CodingSessionStore(tmp_path / ".coding" / "sessions").load("profile-session")
    assert runtime.runtime_profile == "deerflow_v2"
    assert persisted["runtime_profile"] == "deerflow_v2"
    with pytest.raises(AttributeError):
        runtime.runtime_profile = "legacy"  # type: ignore[misc]


def test_resume_preserves_profile_and_rejects_a_mismatched_override(tmp_path: Path) -> None:
    storage = tmp_path / ".coding"
    CodingRuntime(
        session_id="profile-session",
        workspace_root=tmp_path,
        model=object(),
        storage_root=storage,
        runtime_profile="deerflow_v2",
    )

    resumed = CodingRuntime.resume("profile-session", object(), storage)
    assert resumed.runtime_profile == "deerflow_v2"
    with pytest.raises(ValueError, match="does not match"):
        CodingRuntime.resume(
            "profile-session",
            object(),
            storage,
            runtime_profile="legacy",
        )


def test_resume_preserves_sandbox_provider_and_image(tmp_path: Path) -> None:
    storage = tmp_path / ".coding"
    CodingRuntime(
        session_id="sandbox-session",
        workspace_root=tmp_path,
        model=object(),
        storage_root=storage,
        sandbox_provider="container",
        sandbox_image="python:3.12-slim",
    )

    resumed = CodingRuntime.resume("sandbox-session", object(), storage)

    assert resumed.sandbox_provider == "container"
    assert resumed.sandbox_image == "python:3.12-slim"
