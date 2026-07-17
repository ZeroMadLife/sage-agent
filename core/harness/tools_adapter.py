"""Expose a bounded, read-only slice of Sage coding tools to LangChain."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any, cast

from langchain_core.tools import BaseTool, InjectedToolCallId, StructuredTool
from langgraph.types import interrupt
from sage_harness import (
    DeferredToolSetup,
    KnowledgePort,
    MemoryPort,
    SandboxPort,
    SubagentExecutorPort,
    SubagentToolConfig,
    assemble_deferred_tools,
    build_task_tool,
)

from core.coding.engine.events import ToolResultEvent, event_to_dict
from core.coding.runtime import CodingRuntime
from core.coding.tool_executor.approval import ApprovalChoice
from core.coding.tool_executor.executor import ToolExecutor

_DEERFLOW_TOOLS = frozenset(
    {"list_files", "read_file", "search", "write_file", "patch_file", "run_shell"}
)
_LEGACY_AGENT_TOOLS = frozenset({"agent", "send_message", "task_stop"})


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
    sandbox: SandboxPort | None = None,
    subagent_executor: SubagentExecutorPort | None = None,
    subagent_config: SubagentToolConfig | None = None,
    graph_approvals: bool = False,
) -> list[BaseTool]:
    """Build the established resident tool slice without deferred discovery."""
    return list(
        build_deerflow_coding_tool_bundle(
            runtime,
            run_id=run_id,
            knowledge_port=knowledge_port,
            memory_port=memory_port,
            sandbox=sandbox,
            subagent_executor=subagent_executor,
            subagent_config=subagent_config,
            graph_approvals=graph_approvals,
            enable_deferred_tools=False,
        ).tools
    )


def build_deerflow_coding_tool_bundle(
    runtime: CodingRuntime,
    *,
    run_id: str,
    knowledge_port: KnowledgePort | None = None,
    memory_port: MemoryPort | None = None,
    sandbox: SandboxPort | None = None,
    extra_deferred_tools: Sequence[BaseTool] = (),
    subagent_executor: SubagentExecutorPort | None = None,
    subagent_config: SubagentToolConfig | None = None,
    graph_approvals: bool = False,
    enable_deferred_tools: bool = True,
) -> CodingToolBundle:
    """Build V2 tools while preserving Sage execution and approval boundaries."""
    resident_tools: list[BaseTool] = []
    deferred_tools: list[BaseTool] = []
    from core.coding.tools.registry import registered_tool_definitions

    definitions = registered_tool_definitions()
    tool_names = set(_DEERFLOW_TOOLS)
    if subagent_executor is None:
        tool_names.add("agent")
    if knowledge_port is not None and knowledge_port.available:
        tool_names.add("knowledge_learn")
    if enable_deferred_tools:
        tool_names.update(
            name
            for name, registered in runtime.tools.items()
            if registered.deferred
            and name not in _DEERFLOW_TOOLS
            and name not in _LEGACY_AGENT_TOOLS
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
            sandbox=sandbox,
            graph_approvals=graph_approvals,
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

    if subagent_executor is not None:
        resident_tools.append(build_task_tool(subagent_executor, subagent_config))

    deferred_tools.extend(extra_deferred_tools)

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
    sandbox: SandboxPort | None,
    graph_approvals: bool,
) -> BaseTool:
    registered = runtime.tools[name]

    async def invoke_impl(tool_call_id: str, kwargs: Mapping[str, Any]) -> str:
        executor = ToolExecutor(
            tools=runtime.tools,
            workspace=runtime.workspace,
            permission_checker=runtime.permission_checker,
            policy_checker=runtime.policy_checker,
            approval_manager=runtime.approval_manager,
            session_id=runtime.session_id,
            should_stop=lambda: runtime.stop_requested,
            run_id=run_id,
            sandbox=sandbox,
        )
        tool_payload = {"name": name, "args": dict(kwargs)}
        approval_choice: ApprovalChoice | None = None
        if graph_approvals:
            requirement = executor.approval_requirement(tool_payload)
            if requirement is not None:
                approval_id, args_digest = _graph_approval_identity(
                    runtime,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    tool=requirement.tool,
                    args=requirement.args,
                )
                entry = runtime.approval_manager.submit(
                    runtime.session_id,
                    requirement.tool,
                    requirement.args,
                    requirement.description,
                    requirement.pattern_key,
                    approval_id=approval_id,
                    run_id=run_id,
                )
                if entry.result is None:
                    decision = interrupt(
                        {
                            "type": "approval_required",
                            "runtime_profile": "deerflow_v2",
                            "approval_id": entry.approval_id,
                            "tool": requirement.tool,
                            "args": requirement.args,
                            "description": requirement.description,
                            "pattern_key": requirement.pattern_key,
                            "tool_call_id": tool_call_id,
                            "args_digest": args_digest,
                        }
                    )
                else:
                    decision = {
                        "approval_id": entry.approval_id,
                        "choice": entry.result,
                    }
                approval_choice = _graph_approval_choice(
                    decision,
                    approval_id=entry.approval_id,
                )
                server_choice = runtime.approval_manager.consume_resolution(
                    runtime.session_id,
                    entry.approval_id,
                )
                if server_choice is None or server_choice != approval_choice:
                    approval_choice = "deny"
        result = ""
        writer = _stream_writer()
        async for event in executor.execute(
            tool_payload,
            approval_choice=approval_choice,
        ):
            payload = event_to_dict(event)
            if payload.get("type") in {"tool_call", "tool_result"}:
                payload["tool_call_id"] = tool_call_id
            if writer is not None:
                writer(payload)
            if isinstance(event, ToolResultEvent):
                result = event.content
        return result or f"{name} completed without a result"

    if graph_approvals:

        async def graph_invoke(
            tool_call_id: Annotated[str, InjectedToolCallId],
            **kwargs: Any,
        ) -> str:
            return await invoke_impl(tool_call_id, kwargs)

        # ``from __future__ import annotations`` stringifies this nested annotation;
        # StructuredTool needs the runtime object to inject the model tool-call id.
        graph_invoke.__annotations__["tool_call_id"] = Annotated[
            str, InjectedToolCallId
        ]
        coroutine: Any = graph_invoke
    else:

        async def direct_invoke(**kwargs: Any) -> str:
            return await invoke_impl("", kwargs)

        coroutine = direct_invoke
    return StructuredTool.from_function(
        coroutine=coroutine,
        name=name,
        description=registered.description,
        args_schema=args_schema,
        metadata={
            "category": registered.category,
            "risky": registered.risky,
            "sage_source": "coding_registry",
        },
    )


def _graph_approval_identity(
    runtime: CodingRuntime,
    *,
    run_id: str,
    tool_call_id: str,
    tool: str,
    args: Mapping[str, Any],
) -> tuple[str, str]:
    canonical_args = json.dumps(args, sort_keys=True, ensure_ascii=True, default=str)
    args_digest = hashlib.sha256(canonical_args.encode("utf-8")).hexdigest()
    identity = "\0".join(
        (runtime.session_id, run_id, tool_call_id or "missing", tool, args_digest)
    )
    approval_id = f"appr_{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"
    return approval_id, args_digest


def _graph_approval_choice(value: object, *, approval_id: str) -> ApprovalChoice:
    raw_choice: object = value
    if isinstance(value, Mapping):
        if str(value.get("approval_id", "")) != approval_id:
            return "deny"
        raw_choice = value.get("choice")
    choice = str(raw_choice)
    if choice not in {"once", "session", "always", "deny"}:
        return "deny"
    return cast(ApprovalChoice, choice)


def _stream_writer() -> Callable[[Any], None] | None:
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except (KeyError, RuntimeError):
        return None


__all__ = [
    "CodingToolBundle",
    "build_deerflow_coding_tool_bundle",
    "build_deerflow_coding_tools",
]
