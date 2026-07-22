"""Sage application adapter for the DeerFlow-compatible graph runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, cast

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from sage_harness import (
    CapabilityTelemetryMiddleware,
    DeferredToolFilterMiddleware,
    DeferredToolSetup,
    GraphMessageCompactionRequest,
    HarnessConfig,
    HarnessRunContext,
    HarnessRunManager,
    HarnessRunRequest,
    SandboxDescriptor,
    SkillActivationMiddleware,
    SkillCatalog,
    SubagentLifecycleMiddleware,
    SubagentLimits,
    SubagentToolConfig,
    ToolBudgetFinalizationMiddleware,
    create_sage_agent,
    load_graph_message_compaction_plan,
    load_scoped_checkpoint,
    render_deferred_tool_index,
)
from sage_harness.middleware import (
    MiddlewareSpec,
    ToolResultArtifactMiddleware,
    build_default_registry,
)
from sage_harness.ports import ToolArtifactPort
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
        skill_catalog: SkillCatalog | None = None,
        subagent_limits: SubagentLimits | None = None,
        subagent_tool_config: SubagentToolConfig | None = None,
        config: HarnessConfig | None = None,
        artifact_store: ToolArtifactPort | None = None,
        capability_ids_by_tool_name: Mapping[str, str] | None = None,
        capability_revision: str | None = None,
        finalize_after_tool_calls: int | None = None,
    ) -> None:
        self.checkpointer = checkpointer
        self.config = config or HarnessConfig()
        registry = build_default_registry()
        if artifact_store is not None:
            registry = registry.with_spec(
                MiddlewareSpec(
                    "tool_result_artifact",
                    lambda config: ToolResultArtifactMiddleware(artifact_store),
                ),
                after="remote_content_sanitization",
            )
        effective_prompt = system_prompt
        if deferred_setup is not None and deferred_setup.enabled:
            catalog_hash = deferred_setup.catalog_hash
            if catalog_hash is None:
                raise ValueError("Enabled deferred tool setup requires a catalog hash")
            if deferred_setup.selection_index is None:
                raise ValueError("Enabled deferred tool setup requires a selection index")
            capability_bindings = deferred_setup.selection_index.bindings
            registry = registry.with_spec(
                MiddlewareSpec(
                    "deferred_tool_filter",
                    lambda config: DeferredToolFilterMiddleware(
                        capability_bindings,
                        catalog_hash,
                    ),
                ),
                before="tool_error",
            )
            deferred_prompt = render_deferred_tool_index(deferred_setup)
            effective_prompt = "\n\n".join(
                part for part in (system_prompt, deferred_prompt) if part
            )
        if capability_ids_by_tool_name:
            if capability_revision is None:
                raise ValueError("Capability telemetry requires a catalog revision")
            registry = registry.with_spec(
                MiddlewareSpec(
                    "capability_telemetry",
                    lambda config: CapabilityTelemetryMiddleware(
                        capability_ids_by_tool_name,
                        capability_revision,
                    ),
                ),
                before="tool_error",
            )
        if skill_catalog is not None:
            registry = registry.with_spec(
                MiddlewareSpec(
                    "skill_activation",
                    lambda config: SkillActivationMiddleware(skill_catalog),
                ),
                before="input_sanitization",
            )
        if subagent_limits is not None:
            registry = registry.with_spec(
                MiddlewareSpec(
                    "subagent_lifecycle",
                    lambda config: SubagentLifecycleMiddleware(
                        subagent_limits,
                        subagent_tool_config,
                    ),
                ),
                before="durable_context",
            )
        if finalize_after_tool_calls is not None:
            registry = registry.with_spec(
                MiddlewareSpec(
                    "tool_budget_finalization",
                    lambda config: ToolBudgetFinalizationMiddleware(
                        finalize_after_tool_calls,
                    ),
                ),
                before="run_budget",
            )
        self.graph = create_sage_agent(
            model=model,
            tools=tools,
            system_prompt=effective_prompt,
            registry=registry,
            config=self.config,
            checkpointer=checkpointer,
        )
        self.manager = HarnessRunManager(cast(StreamableGraph, self.graph))

    async def stream_turn(
        self,
        *,
        session_id: str,
        run_id: str,
        owner_id: str = "local",
        workspace_id: str,
        workspace_path: str,
        content: str,
        resume: bool = False,
        resume_value: object | None = None,
        resume_attempt: int = 0,
        surface: str = "coding",
        surface_context: Mapping[str, Any] | None = None,
        durable_context: Mapping[str, Any] | None = None,
        graph_compaction: Mapping[str, Any] | None = None,
        sandbox: SandboxDescriptor | None = None,
    ) -> AsyncIterator[RunEvent]:
        """Yield only public graph events; the host adds the terminal event."""
        context = HarnessRunContext(
            thread_id=session_id,
            run_id=run_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            workspace_path=workspace_path,
            surface=surface,
            metadata={
                "surface_context": dict(surface_context or {}),
                "durable_context": dict(durable_context or {}),
            },
        )
        if sandbox is not None and sandbox.workspace_id != workspace_id:
            raise ValueError("sandbox workspace_id does not match run context")
        await load_scoped_checkpoint(self.checkpointer, context)
        state_update: dict[str, object] = {}
        if sandbox is not None:
            state_update.update(
                {
                    "thread_data": {
                        "owner_id": owner_id,
                        "workspace_id": workspace_id,
                        "thread_id": session_id,
                        "workspace_path": workspace_path,
                    },
                    "sandbox": {"sandbox_id": sandbox.sandbox_id},
                }
            )
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
                context=context,
            )
            state_update.update(plan.state_update())
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
            recursion_limit=self.config.recursion_limit,
            timeout_seconds=self.config.max_run_seconds,
            state_update=state_update,
            resume=resume,
            resume_value=resume_value,
        )
        if resume_attempt < 0:
            raise ValueError("resume_attempt must be non-negative")
        adapter = HarnessEventAdapter(
            session_id=session_id,
            run_id=run_id,
            stream_namespace=f"resume-{resume_attempt}" if resume else "initial",
            seen_tool_call_ids=(
                await _checkpoint_pending_tool_call_ids(self.checkpointer, context)
                if resume
                else ()
            ),
        )
        async for item in self.manager.stream(request):
            if compaction_event is not None:
                yield compaction_event
                compaction_event = None
            for event in adapter.adapt(item):
                yield event
        for event in adapter.finish():
            yield event


async def _checkpoint_pending_tool_call_ids(
    checkpointer: BaseCheckpointSaver[Any],
    context: HarnessRunContext,
) -> tuple[str, ...]:
    checkpoint = await load_scoped_checkpoint(checkpointer, context)
    if checkpoint is None:
        return ()
    channels: object = checkpoint.checkpoint.get("channel_values", {})
    if not isinstance(channels, Mapping):
        return ()
    messages = channels.get("messages", ())
    if not isinstance(messages, Sequence) or isinstance(messages, str | bytes):
        return ()
    pending_tool_call_ids: dict[str, None] = {}
    for message in messages:
        calls = getattr(message, "tool_calls", None)
        if calls is None and isinstance(message, Mapping):
            calls = message.get("tool_calls", ())
        if not isinstance(calls, Sequence) or isinstance(calls, str | bytes):
            calls = ()
        for call in calls:
            if isinstance(call, Mapping):
                tool_call_id = str(call.get("id", "")).strip()
                if tool_call_id:
                    pending_tool_call_ids[tool_call_id] = None
        result_call_id = getattr(message, "tool_call_id", None)
        if result_call_id is None and isinstance(message, Mapping):
            result_call_id = message.get("tool_call_id")
        if isinstance(result_call_id, str) and result_call_id.strip():
            pending_tool_call_ids.pop(result_call_id.strip(), None)
    return tuple(pending_tool_call_ids)


__all__ = ["SageHarnessRuntimeAdapter"]
