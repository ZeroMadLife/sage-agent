"""Sage application adapter for the DeerFlow-compatible graph runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from sage_harness import (
    DeferredToolFilterMiddleware,
    DeferredToolSetup,
    GraphMessageCompactionRequest,
    HarnessRunContext,
    HarnessRunManager,
    HarnessRunRequest,
    create_sage_agent,
    load_graph_message_compaction_plan,
    render_deferred_tool_index,
)
from sage_harness.middleware import MiddlewareSpec, build_default_registry
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
        deferred_setup: DeferredToolSetup | None = None,
    ) -> None:
        self.checkpointer = checkpointer
        registry = build_default_registry()
        effective_prompt = system_prompt
        if deferred_setup is not None and deferred_setup.enabled:
            catalog_hash = deferred_setup.catalog_hash
            if catalog_hash is None:
                raise ValueError("Enabled deferred tool setup requires a catalog hash")
            registry = registry.with_spec(
                MiddlewareSpec(
                    "deferred_tool_filter",
                    lambda config: DeferredToolFilterMiddleware(
                        deferred_setup.deferred_names,
                        catalog_hash,
                    ),
                ),
                before="tool_error",
            )
            deferred_prompt = render_deferred_tool_index(deferred_setup)
            effective_prompt = "\n\n".join(
                part for part in (system_prompt, deferred_prompt) if part
            )
        self.graph = create_sage_agent(
            model=model,
            tools=tools,
            system_prompt=effective_prompt,
            registry=registry,
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
        graph_compaction: Mapping[str, Any] | None = None,
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
        state_update: Mapping[str, object] = {}
        compaction_event: RunEvent | None = None
        if graph_compaction is not None:
            compaction_request = GraphMessageCompactionRequest(
                compaction_id=str(graph_compaction.get("compaction_id", "")),
                summary_text=str(graph_compaction.get("summary_text", "")),
                keep_recent_messages=int(graph_compaction.get("keep_recent_messages", 12)),
            )
            plan = await load_graph_message_compaction_plan(
                self.checkpointer,
                thread_id=session_id,
                request=compaction_request,
            )
            state_update = plan.state_update()
            compaction_event = RunEvent(
                kind="context",
                status="completed",
                payload={
                    "type": "graph_context_compacted",
                    "session_id": session_id,
                    "run_id": run_id,
                    "compaction_id": plan.compaction_id,
                    "removed_message_count": plan.removed_message_count,
                    "preserved_message_count": plan.preserved_message_count,
                },
                event_id=f"harness:{run_id}:graph-compaction:{plan.compaction_id}",
            )
        request = HarnessRunRequest(
            thread_id=session_id,
            run_id=run_id,
            context=context,
            message=content,
            state_update=state_update,
        )
        adapter = HarnessEventAdapter(session_id=session_id, run_id=run_id)
        async for item in self.manager.stream(request):
            if compaction_event is not None:
                yield compaction_event
                compaction_event = None
            for event in adapter.adapt(item):
                yield event


__all__ = ["SageHarnessRuntimeAdapter"]
