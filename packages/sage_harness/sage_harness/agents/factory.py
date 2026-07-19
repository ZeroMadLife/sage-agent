"""Pure-argument factory for the reusable Sage agent graph."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from sage_harness.config import HarnessConfig, HarnessRunContext
from sage_harness.middleware.registry import MiddlewareRegistry, build_default_registry
from sage_harness.state import SageThreadState

ToolLike = BaseTool | Callable[..., Any] | dict[str, Any]
Middleware = AgentMiddleware[Any, Any, Any]


def create_sage_agent(
    model: BaseChatModel,
    tools: Sequence[ToolLike] | None = None,
    *,
    system_prompt: str | None = None,
    config: HarnessConfig | None = None,
    registry: MiddlewareRegistry | None = None,
    middleware: Sequence[Middleware] | None = None,
    state_schema: type[SageThreadState] = SageThreadState,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    name: str = "sage",
) -> CompiledStateGraph[Any, HarnessRunContext, Any, Any]:
    """Build a LangChain agent without reading files, globals, or Sage stores."""
    if middleware is not None and registry is not None:
        raise ValueError("middleware is a full takeover and cannot be combined with registry")
    effective_config = config or HarnessConfig()
    effective_middleware = (
        list(middleware)
        if middleware is not None
        else (registry or build_default_registry()).build(effective_config)
    )
    return create_agent(
        model=model,
        tools=list(tools) if tools else None,
        system_prompt=system_prompt,
        middleware=effective_middleware,
        state_schema=state_schema,
        context_schema=HarnessRunContext,
        checkpointer=checkpointer,
        name=name,
    )


__all__ = ["create_sage_agent"]
