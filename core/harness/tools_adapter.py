"""Expose a bounded, read-only slice of Sage coding tools to LangChain."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from sage_harness import (
    DeferredToolSetup,
    KnowledgePort,
    MemoryPort,
    assemble_deferred_tools,
)

from core.coding.engine.events import ToolResultEvent, event_to_dict
from core.coding.runtime import CodingRuntime
from core.coding.tool_executor.executor import ToolExecutor

_DEERFLOW_TOOLS = frozenset(
    {"list_files", "read_file", "search", "write_file", "patch_file", "run_shell", "agent"}
)


@dataclass(frozen=True, slots=True)
class CodingToolBundle:
    """Executable graph tools plus model-visibility policy for one turn."""

    tools: tuple[BaseTool, ...]
    deferred_setup: DeferredToolSetup


def build_deerflow_coding_tools(
    runtime: CodingRuntime,
    *,
    run_id: str,
    knowledge_port: KnowledgePort | None = None,
    memory_port: MemoryPort | None = None,
) -> list[BaseTool]:
    """Build the established resident tool slice without deferred discovery."""
    return list(
        build_deerflow_coding_tool_bundle(
            runtime,
            run_id=run_id,
            knowledge_port=knowledge_port,
            memory_port=memory_port,
            enable_deferred_tools=False,
        ).tools
    )


def build_deerflow_coding_tool_bundle(
    runtime: CodingRuntime,
    *,
    run_id: str,
    knowledge_port: KnowledgePort | None = None,
    memory_port: MemoryPort | None = None,
    enable_deferred_tools: bool = True,
) -> CodingToolBundle:
    """Build V2 tools while preserving Sage execution and approval boundaries."""
    resident_tools: list[BaseTool] = []
    deferred_tools: list[BaseTool] = []
    from core.coding.tools.registry import registered_tool_definitions

    definitions = registered_tool_definitions()
    tool_names = set(_DEERFLOW_TOOLS)
    if knowledge_port is not None and knowledge_port.available:
        tool_names.add("knowledge_learn")
    if enable_deferred_tools:
        tool_names.update(
            name
            for name, registered in runtime.tools.items()
            if registered.deferred
            and name not in _DEERFLOW_TOOLS
            and not (name == "remember" and memory_port is not None)
        )
    for name in sorted(tool_names):
        registered = runtime.tools.get(name)
        definition = definitions.get(name)
        if registered is None or definition is None:
            continue
        tool = _build_runtime_tool(
            runtime,
            run_id=run_id,
            name=name,
            args_schema=definition.schema_model,
        )
        target = (
            deferred_tools
            if enable_deferred_tools and registered.deferred and name not in _DEERFLOW_TOOLS
            else resident_tools
        )
        target.append(tool)
    if knowledge_port is not None:
        search_definition = definitions.get("knowledge_search")
        if search_definition is not None:

            async def search_knowledge(
                query: str,
                top_k: int = 8,
                token_budget: int = 3000,
            ) -> str:
                result = await knowledge_port.search(
                    query,
                    workspace_id=knowledge_port.workspace_id,
                    token_budget=token_budget,
                    top_k=top_k,
                )
                payload = {
                    "status": result.status,
                    "query": result.query,
                    "used_tokens": result.used_tokens,
                    "token_budget": result.token_budget,
                    "omitted_count": result.omitted_count,
                    "instruction": (
                        "Use only the cited excerpts for knowledge-base claims. "
                        "Call knowledge_learn only after explicit user confirmation."
                        if result.evidence
                        else "The knowledge base is unavailable."
                        if result.status == "unavailable"
                        else "The knowledge base has no evidence for this query. Say so explicitly."
                    ),
                    "citations": [
                        {
                            "citation_id": item.citation_id,
                            "rank": item.metadata.get("rank", index),
                            "page_revision": item.page_revision,
                            "source_revision": item.source_revision,
                            "source_kind": item.metadata.get("source_kind", ""),
                            "source_relative_path": item.metadata.get(
                                "source_relative_path", ""
                            ),
                            "title": item.metadata.get("title", item.citation_id),
                            "heading_path": item.metadata.get("heading_path", ()),
                            "block_id": item.metadata.get("block_id", ""),
                            "excerpt": item.content,
                            "token_count": item.metadata.get("token_count", 0),
                            "truncated": item.metadata.get("truncated", False),
                        }
                        for index, item in enumerate(result.evidence, start=1)
                    ],
                }
                return json.dumps(payload, ensure_ascii=False, indent=2)

            resident_tools.append(
                StructuredTool.from_function(
                    coroutine=search_knowledge,
                    name="knowledge_search",
                    description=search_definition.description,
                    args_schema=search_definition.schema_model,
                    metadata={"category": "knowledge", "sage_source": "knowledge_port"},
                )
            )
    if memory_port is not None:
        remember_definition = definitions.get("remember")
        if remember_definition is not None:

            async def propose_memory(
                fact: str,
                topic: str = "project-conventions",
            ) -> str:
                receipt = await memory_port.propose(
                    runtime.session_id,
                    run_id,
                    fact,
                    topic=topic,
                )
                return json.dumps(
                    {
                        "proposal_id": receipt.proposal_id,
                        "status": receipt.status,
                        "requires_user_confirmation": receipt.status == "pending",
                        "session_id": receipt.thread_id,
                        "run_id": receipt.run_id,
                        "reflection_id": receipt.reflection_id,
                        "candidate_count": receipt.candidate_count,
                        "base_revision": receipt.base_revision,
                    },
                    ensure_ascii=True,
                )

            remember_tool = StructuredTool.from_function(
                coroutine=propose_memory,
                name="remember",
                description=(
                    "Propose a stable workspace convention or decision for user review. "
                    "This tool never writes durable memory until the proposal is approved."
                ),
                args_schema=remember_definition.schema_model,
                metadata={"category": "memory", "sage_source": "memory_port"},
            )
            (deferred_tools if enable_deferred_tools else resident_tools).append(remember_tool)

    graph_tools, deferred_setup = assemble_deferred_tools(
        resident_tools,
        deferred_tools,
        enabled=enable_deferred_tools,
    )
    return CodingToolBundle(tuple(graph_tools), deferred_setup)


def _build_runtime_tool(
    runtime: CodingRuntime,
    *,
    run_id: str,
    name: str,
    args_schema: Any,
) -> BaseTool:
    registered = runtime.tools[name]

    async def invoke(**kwargs: Any) -> str:
        executor = ToolExecutor(
            tools=runtime.tools,
            workspace=runtime.workspace,
            permission_checker=runtime.permission_checker,
            policy_checker=runtime.policy_checker,
            approval_manager=runtime.approval_manager,
            session_id=runtime.session_id,
            should_stop=lambda: runtime.stop_requested,
            run_id=run_id,
        )
        result = ""
        writer = _stream_writer()
        async for event in executor.execute({"name": name, "args": dict(kwargs)}):
            payload = event_to_dict(event)
            if writer is not None:
                writer(payload)
            if isinstance(event, ToolResultEvent):
                result = event.content
                if name == "agent" and writer is not None:
                    agent_event = _agent_started_event(result)
                    if agent_event is not None:
                        writer(agent_event)
        return result or f"{name} completed without a result"

    return StructuredTool.from_function(
        coroutine=invoke,
        name=name,
        description=registered.description,
        args_schema=args_schema,
        metadata={
            "category": registered.category,
            "risky": registered.risky,
            "sage_source": "coding_registry",
        },
    )


def _stream_writer() -> Callable[[Any], None] | None:
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except (KeyError, RuntimeError):
        return None


def _agent_started_event(content: str) -> dict[str, Any] | None:
    """Project a worker launch into a small public event, never its full trace."""
    import json

    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or not str(payload.get("task_id", "")).strip():
        return None
    return {
        "type": "agent_started",
        "agent_run_id": str(payload["task_id"]),
        "status": str(payload.get("status", "started")),
        "description": str(payload.get("description", ""))[:400],
    }


__all__ = [
    "CodingToolBundle",
    "build_deerflow_coding_tool_bundle",
    "build_deerflow_coding_tools",
]
