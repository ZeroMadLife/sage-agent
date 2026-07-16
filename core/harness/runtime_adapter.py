"""Sage application adapter for the DeerFlow-compatible graph runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from sage_harness import HarnessRunContext, HarnessRunManager, HarnessRunRequest, create_sage_agent
from sage_harness.runtime.manager import StreamableGraph

from core.coding.run_coordinator import RunEvent
from core.harness.event_adapter import HarnessEventAdapter


class SageHarnessRuntimeAdapter:
    """Build and run one graph while keeping Sage's event contract at the edge."""

    def __init__(
        self,
        *,
        model: BaseChatModel,
        checkpointer: BaseCheckpointSaver[Any],
        tools: Sequence[BaseTool] = (),
        system_prompt: str | None = None,
    ) -> None:
        self.graph = create_sage_agent(
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            checkpointer=checkpointer,
        )
        self.manager = HarnessRunManager(cast(StreamableGraph, self.graph))

    async def stream_turn(
        self,
        *,
        session_id: str,
        run_id: str,
        workspace_id: str,
        workspace_path: str,
        content: str,
        surface: str = "coding",
        surface_context: Mapping[str, Any] | None = None,
        durable_context: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[RunEvent]:
        """Yield only public graph events; the host adds the terminal event."""
        context = HarnessRunContext(
            thread_id=session_id,
            run_id=run_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            surface=surface,
            metadata={
                "surface_context": dict(surface_context or {}),
                "durable_context": dict(durable_context or {}),
            },
        )
        request = HarnessRunRequest(
            thread_id=session_id,
            run_id=run_id,
            context=context,
            message=content,
        )
        adapter = HarnessEventAdapter(session_id=session_id, run_id=run_id)
        async for item in self.manager.stream(request):
            for event in adapter.adapt(item):
                yield event


__all__ = ["SageHarnessRuntimeAdapter"]
