"""Coding runtime reasoning selection tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.coding.context import ContextBusyError
from core.coding.runtime import CodingRuntime


class Model:
    def __init__(self, model_id: str, reasoning_mode: str) -> None:
        self.model_id = model_id
        self.reasoning_mode = reasoning_mode

    async def complete(self, prompt: str) -> str:
        del prompt
        return "<final>done</final>"


def _runtime(tmp_path: Path) -> tuple[CodingRuntime, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []

    def factory(model_id: str, *, reasoning_mode: str = "off") -> Model:
        calls.append((model_id, reasoning_mode))
        return Model(model_id, reasoning_mode)

    runtime = CodingRuntime(
        session_id="session-1",
        workspace_root=tmp_path,
        model=Model("model-a", "off"),
        storage_root=tmp_path / ".coding",
        model_factory=factory,
        model_spec="model-a",
        model_reasoning_modes={"model-a": ("low", "high"), "model-b": ()},
    )
    return runtime, calls


def test_reasoning_selection_persists_and_workers_use_current_selection(tmp_path: Path) -> None:
    runtime, calls = _runtime(tmp_path)

    runtime.switch_reasoning("high")
    worker_model = runtime.worker_manager.model_factory()

    assert runtime.reasoning_mode == "high"
    assert runtime.session["reasoning_mode"] == "high"
    assert worker_model.reasoning_mode == "high"
    assert calls == [("model-a", "high"), ("model-a", "high")]


def test_model_switch_falls_back_to_off_when_reasoning_is_not_supported(
    tmp_path: Path,
) -> None:
    runtime, _ = _runtime(tmp_path)
    runtime.switch_reasoning("high")

    runtime.switch_model("model-b", lambda: Model("model-b", "off"))

    assert runtime.model_spec == "model-b"
    assert runtime.reasoning_mode == "off"
    assert runtime.session["reasoning_mode"] == "off"


def test_reasoning_rejects_unsupported_and_busy_changes(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path)

    with pytest.raises(ValueError, match="unsupported reasoning mode"):
        runtime.switch_reasoning("medium")

    runtime.active_run_id = "run-1"
    with pytest.raises(ContextBusyError, match="context operation is active"):
        runtime.switch_reasoning("low")


def test_workspace_reminders_prioritize_dot_sage_instructions(tmp_path: Path) -> None:
    runtime, _ = _runtime(tmp_path)
    (tmp_path / ".sage").mkdir(exist_ok=True)
    (tmp_path / ".sage" / "SAGE.md").write_text("project settings", encoding="utf-8")
    (tmp_path / "SAGE.md").write_text("legacy settings", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agent settings", encoding="utf-8")

    reminders = runtime._workspace_reminders()

    assert reminders == [
        ".sage/SAGE.md:\nproject settings",
        "SAGE.md:\nlegacy settings",
        "AGENTS.md:\nagent settings",
    ]
