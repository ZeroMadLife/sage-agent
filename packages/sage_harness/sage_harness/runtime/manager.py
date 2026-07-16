"""Long-running LangGraph invocation manager."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Protocol

from sage_harness.config import HarnessRunContext
from sage_harness.runtime.checkpoint import thread_config
from sage_harness.runtime.events import HarnessStreamItem, normalize_stream_item


class StreamableGraph(Protocol):
    """Subset of CompiledStateGraph required by the runtime manager."""

    def astream(
        self,
        input: Mapping[str, Any],
        *,
        config: Mapping[str, Any],
        context: HarnessRunContext,
        stream_mode: list[str],
    ) -> AsyncIterator[Any]: ...


@dataclass(frozen=True, slots=True)
class HarnessRunRequest:
    """Server-owned input for one graph run."""

    thread_id: str
    run_id: str
    context: HarnessRunContext
    message: str
    recursion_limit: int = 100
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.run_id).strip():
            raise ValueError("run_id must not be empty")
        if not str(self.message).strip():
            raise ValueError("message must not be empty")
        if self.context.thread_id != self.thread_id:
            raise ValueError("run context thread_id does not match request")


class HarnessRunManager:
    """Own graph invocation configuration while Sage owns durable run leases."""

    STREAM_MODES: ClassVar[list[str]] = ["values", "messages", "custom"]

    def __init__(self, graph: StreamableGraph) -> None:
        self.graph = graph

    async def stream(self, request: HarnessRunRequest) -> AsyncIterator[HarnessStreamItem]:
        """Stream graph values/messages/custom events in deterministic order."""
        config = thread_config(request.thread_id, recursion_limit=request.recursion_limit)
        if request.metadata:
            config["metadata"] = dict(request.metadata)
        state = {"messages": [{"role": "user", "content": request.message}]}
        sequence = 0
        async for raw in self.graph.astream(
            state,
            config=config,
            context=request.context,
            stream_mode=self.STREAM_MODES,
        ):
            sequence += 1
            yield normalize_stream_item(raw, sequence)


__all__ = ["HarnessRunManager", "HarnessRunRequest"]
