from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from core.coding.context import ContextManager, WorkspaceContext
from core.coding.engine.engine import Engine
from core.coding.tool_executor import PermissionChecker, ToolPolicyChecker


class NeverCalledModel:
    calls = 0

    async def complete(self, prompt: str) -> str:
        self.calls += 1
        return "<final>unexpected</final>"


class EmergencyPrepared:
    allow_model_request = False
    projected_history: ClassVar[list[dict[str, object]]] = []
    events: ClassVar[tuple[object, ...]] = ()


@pytest.mark.asyncio
async def test_engine_emergency_hook_stops_before_model_requested(tmp_path: Path) -> None:
    workspace = WorkspaceContext(tmp_path)
    model = NeverCalledModel()
    engine = Engine(
        model=model,
        workspace=workspace,
        tools={},
        context_manager=ContextManager(),
        permission_checker=PermissionChecker(),
        policy_checker=ToolPolicyChecker(workspace),
        history=[],
        before_model_request=lambda history: EmergencyPrepared(),
    )

    events = [event async for event in engine.run_turn("hello")]

    assert model.calls == 0
    assert not any(event["type"] == "model_requested" for event in events)
    assert [event["type"] for event in events] == ["error", "cancelled"]
