"""Assertions for benchmark scenarios.

All assertions are pure functions returning booleans so the runner can compose
them without raising, keeping one failing scenario from aborting the run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def assert_no_write(events: list[dict[str, Any]]) -> bool:
    """No write_file or patch_file tool call succeeded."""
    for event in events:
        if (
            event.get("type") == "tool_result"
            and event.get("tool") in {"write_file", "patch_file"}
            and not event.get("is_error")
        ):
            return False
    return True


def assert_files_match(workspace_root: Path, expected: dict[str, str]) -> bool:
    """Check that workspace files match expected content (trailing ws tolerant)."""
    for path, expected_content in expected.items():
        fpath = workspace_root / path
        if not fpath.is_file():
            return False
        actual = fpath.read_text(encoding="utf-8").strip()
        if actual != expected_content.strip():
            return False
    return True


def assert_tool_calls_match(events: list[dict[str, Any]], expected: list[str]) -> bool:
    """Check that the executed tool-call sequence matches expected."""
    actual = [event.get("tool", "") for event in events if event.get("type") == "tool_call"]
    return actual == expected


def assert_policy_denial(events: list[dict[str, Any]]) -> bool:
    """Check that a policy denial occurred (plan-mode or prior-read guard)."""
    plan_markers = ("plan_mode", "prior_read_required")
    for event in events:
        if event.get("type") == "tool_result" and event.get("is_error"):
            content = str(event.get("content", ""))
            if any(marker in content for marker in plan_markers):
                return True
    return False


def assert_approval_requested(events: list[dict[str, Any]]) -> bool:
    """Check that an approval was requested via the approval flow."""
    return any(event.get("type") == "approval_required" for event in events)


def assert_run_finished(events: list[dict[str, Any]]) -> bool:
    """Check that the runtime emitted a run_finished terminal event.

    The real CodingRuntime always yields ``run_finished`` at the end of a turn
    (after the workspace diff and before ``turn_finished``), even on errors.
    """
    return any(event.get("type") == "run_finished" for event in events)


def assert_diff_ready(events: list[dict[str, Any]]) -> bool:
    """Check that the runtime emitted a workspace_diff_ready event.

    The runtime produces a bounded workspace diff artifact from before/after
    snapshots after each run and surfaces it before ``run_finished``.
    """
    return any(event.get("type") == "workspace_diff_ready" for event in events)


def assert_memory_saved(workspace_root: Path, fact: str) -> bool:
    """Check that a memory fact was saved under the workspace storage tree.

    Durable memory writes to ``<storage_root>/memory/<workspace_id>/``. The
    runner pins storage_root to ``<workspace_root>/.coding``, so we scan that
    tree for the fact text.
    """
    memory_root = workspace_root / ".coding" / "memory"
    if not memory_root.is_dir():
        return False
    return any(fact in path.read_text(encoding="utf-8") for path in memory_root.rglob("*.md"))
