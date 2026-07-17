"""Coding-agent REST and WebSocket routes."""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncGenerator, Mapping
from contextlib import suppress
from inspect import signature
from pathlib import Path
from typing import Any, Literal, cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket
from langchain_core.tools import BaseTool
from sage_harness import (
    HarnessConfig,
    McpCatalogPort,
    McpManager,
    McpScope,
    SubagentLimits,
    SubagentToolConfig,
)
from starlette.requests import HTTPConnection
from starlette.websockets import WebSocketDisconnect

from api.cloud_dependencies import SESSION_COOKIE
from api.cloud_model_context import (
    combined_capabilities,
    combined_catalog,
    combined_model_factory,
    combined_reasoning_modes,
    load_account_model_context,
)
from api.harness_context import SurfaceContextValidationError, validate_surface_context
from api.schemas import (
    CodingActiveRun,
    CodingApprovalRespondRequest,
    CodingApprovalResponse,
    CodingContextCompactRequest,
    CodingContextCompactResponse,
    CodingContextSnapshot,
    CodingFileContentResponse,
    CodingFileEntry,
    CodingFilesResponse,
    CodingGitStatusResponse,
    CodingMcpServer,
    CodingMcpServersResponse,
    CodingMemoryCandidate,
    CodingMemoryEvent,
    CodingMemoryProposal,
    CodingMemoryProposalDecisionRequest,
    CodingMemoryProposalDetail,
    CodingMemoryProposalsResponse,
    CodingMemoryProposalTransitionRequest,
    CodingModel,
    CodingModelsResponse,
    CodingModelSwitchRequest,
    CodingProviderModelResponse,
    CodingProviderResponse,
    CodingProviderSettingsResponse,
    CodingReasoningSwitchRequest,
    CodingRunDetailResponse,
    CodingRunsResponse,
    CodingRunStopRequest,
    CodingRunSummary,
    CodingSessionMessage,
    CodingSessionMessagesResponse,
    CodingSessionMetadataRequest,
    CodingSessionRequest,
    CodingSessionResponse,
    CodingSessionsResponse,
    CodingSessionSummary,
    CodingSkillDetailResponse,
    CodingSkillsResponse,
    CodingSkillSummary,
    CodingTimelineEvent,
    CodingTimelineResponse,
    CodingUsageSummary,
    ErrorEvent,
    PermissionModeSwitchRequest,
    UserMessage,
)
from core.cloud.auth.repository import CloudRepository
from core.coding.context import ContextBusyError
from core.coding.harness import CodingHarnessStageProjector
from core.coding.memory import workspace_id_from_path
from core.coding.persistence import (
    CodingSessionStore,
    MemoryConflictError,
    MemoryEvent,
    MemoryProposal,
    MemoryStoreError,
)
from core.coding.persistence.session_event_journal import SessionEvent
from core.coding.persistence.tool_result_store import ToolResultStore
from core.coding.provider_settings import SageProviderSettings, SageProviderSettingsStore
from core.coding.run_coordinator import ActiveRunConflictError, RunEvent
from core.coding.runtime import CodingRuntime
from core.harness import RuntimeProfile, normalize_runtime_profile
from core.harness.context_adapter import (
    build_deerflow_durable_context,
    build_deerflow_system_prompt,
    context_status_event,
)
from core.harness.knowledge_adapter import CodingKnowledgePort
from core.harness.mcp_adapter import mcp_catalog_event
from core.harness.memory_adapter import CodingMemoryPort
from core.harness.runtime_adapter import SageHarnessRuntimeAdapter
from core.harness.sandbox_factory import create_coding_sandbox
from core.harness.subagent_adapter import CodingSubagentExecutor
from core.harness.tools_adapter import build_deerflow_coding_tool_bundle

_SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def _require_enabled_runtime_profile(value: object, request: Request) -> RuntimeProfile:
    """Resolve a requested profile and enforce the server-owned rollout gate."""
    profile = normalize_runtime_profile(value)
    if profile == "deerflow_v2" and not bool(request.app.state.coding_deerflow_v2_enabled):
        raise HTTPException(status_code=422, detail="deerflow_v2 runtime profile is disabled")
    app_env = str(getattr(request.app.state, "cloud_app_env", "development")).lower()
    sandbox_provider = str(
        getattr(request.app.state, "coding_sandbox_provider", "local_workspace")
    ).strip().lower()
    if (
        profile == "deerflow_v2"
        and app_env not in {"development", "test"}
        and sandbox_provider != "container"
    ):
        raise HTTPException(
            status_code=422,
            detail="deerflow_v2 requires an isolated sandbox outside development/test",
        )
    return profile


def _available_runtime_profiles(request: Request) -> list[RuntimeProfile]:
    """Advertise only runtime profiles that this deployment can safely create."""
    profiles: list[RuntimeProfile] = ["legacy"]
    if not bool(getattr(request.app.state, "coding_deerflow_v2_enabled", False)):
        return profiles
    app_env = str(getattr(request.app.state, "cloud_app_env", "development")).lower()
    sandbox_provider = str(
        getattr(request.app.state, "coding_sandbox_provider", "local_workspace")
    ).strip().lower()
    if app_env in {"development", "test"} or sandbox_provider == "container":
        profiles.append("deerflow_v2")
    return profiles


async def _enforce_coding_session_owner(connection: HTTPConnection) -> None:
    session_id = str(
        connection.path_params.get("session_id", "")
        or connection.query_params.get("session_id", "")
    )
    if not session_id:
        return
    sessions: dict[str, CodingRuntime] = connection.app.state.coding_sessions
    runtime = sessions.get(session_id)
    owner_user_id = runtime.owner_user_id if runtime is not None else None
    if owner_user_id is None:
        store = CodingSessionStore(
            Path(connection.app.state.coding_storage_root) / "sessions"
        )
        try:
            persisted = store.load(session_id)
        except (FileNotFoundError, ValueError):
            return
        owner_user_id = str(persisted.get("owner_user_id", "")).strip() or None
    if owner_user_id is None:
        return
    cloud = getattr(connection.app.state, "cloud_repository", None)
    user = (
        await cloud.authenticated_user(connection.cookies.get(SESSION_COOKIE, ""))
        if isinstance(cloud, CloudRepository)
        else None
    )
    if user is None or user.user_id != owner_user_id:
        raise HTTPException(status_code=404, detail=f"Unknown coding session: {session_id}")


router = APIRouter(dependencies=[Depends(_enforce_coding_session_owner)])


def _valid_session_id(session_id: str) -> bool:
    return _SESSION_ID.fullmatch(session_id) is not None


def _require_valid_session_id(session_id: str) -> None:
    if not _valid_session_id(session_id):
        raise HTTPException(status_code=422, detail="invalid coding session id")


def _coding_knowledge_store(connection: HTTPConnection) -> Any | None:
    """Do not bypass the Knowledge API's production tenant-isolation gate."""
    if str(getattr(connection.app.state, "cloud_app_env", "development")) == "production":
        return None
    return getattr(connection.app.state, "knowledge_store", None)


async def _runtime_timeline_events(
    runtime: CodingRuntime,
    *,
    content: str,
    skill_prompt: str | None,
    command: str,
    arguments: str,
    run_id: str,
    surface_context: Mapping[str, Any] | None,
    harness_checkpointer: Any | None = None,
    harness_config: HarnessConfig | None = None,
    mcp_catalog: McpCatalogPort | None = None,
    app_env: str = "development",
    resume_value: object | None = None,
    resume_attempt: int = 0,
) -> AsyncGenerator[RunEvent, None]:
    """Project a complete runtime generator into durable nonterminal events."""
    if runtime.runtime_profile == "deerflow_v2":
        if harness_checkpointer is None:
            raise RuntimeError("deerflow_v2 checkpointer is not configured")
        evidence_started = False
        evidence_finished = False
        evidence_start_time = time.monotonic()
        try:
            await runtime.begin_harness_evidence(run_id)
            evidence_started = True
            async for graph_event in _deerflow_timeline_events(
                runtime,
                content=content,
                run_id=run_id,
                surface_context=surface_context,
                checkpointer=harness_checkpointer,
                harness_config=harness_config,
                mcp_catalog=mcp_catalog,
                app_env=app_env,
                resume_value=resume_value,
                resume_attempt=resume_attempt,
            ):
                if graph_event.kind == "terminal":
                    diff_payload = await runtime.finish_harness_evidence(
                        run_id,
                        status=graph_event.status,
                        duration_ms=int(
                            (time.monotonic() - evidence_start_time) * 1000
                        ),
                    )
                    evidence_finished = True
                    yield RunEvent(
                        kind="tool",
                        status="completed",
                        payload=diff_payload,
                        event_id=f"harness:{run_id}:workspace-diff",
                    )
                else:
                    await runtime.append_harness_evidence(
                        run_id,
                        graph_event.payload,
                    )
                yield graph_event
        except asyncio.CancelledError:
            if evidence_started and not evidence_finished:
                await runtime.abort_harness_evidence(
                    run_id,
                    status="cancelled",
                    duration_ms=int(
                        (time.monotonic() - evidence_start_time) * 1000
                    ),
                )
            raise
        except Exception:
            if evidence_started and not evidence_finished:
                await runtime.abort_harness_evidence(
                    run_id,
                    status="error",
                    duration_ms=int(
                        (time.monotonic() - evidence_start_time) * 1000
                    ),
                )
            raise
        return
    terminal_status = "completed"
    harness = CodingHarnessStageProjector(run_id)
    yield RunEvent(
        kind="user",
        status="completed",
        payload={"type": "user", "content": content},
    )
    if skill_prompt:
        yield RunEvent(
            kind="system",
            status="completed",
            payload={"type": "skill_invoked", "skill": command, "arguments": arguments},
        )
    for stage_event in harness.start():
        yield stage_event
    try:
        async for raw_event in runtime.run_turn(
            content,
            skill_prompt=skill_prompt,
            surface_context=surface_context,
            run_id=run_id,
        ):
            event: dict[str, Any] = dict(raw_event)
            event_type = str(event.get("type", ""))
            if event_type == "run_finished":
                candidate = str(event.get("status", "completed"))
                if candidate == "retryable":
                    terminal_status = "interrupted"
                elif candidate in {"completed", "cancelled", "interrupted", "error"}:
                    terminal_status = candidate
            elif event_type == "error":
                terminal_status = "error"
            for stage_event in harness.before(event):
                yield stage_event
            yield RunEvent(
                kind=_timeline_kind(event_type),
                status=_timeline_status(event_type, event),
                payload=dict(event),
            )
            for stage_event in harness.after(event):
                yield stage_event
    except Exception:
        for stage_event in harness.finish("error"):
            yield stage_event
        raise
    for stage_event in harness.finish(terminal_status):
        yield stage_event
    yield RunEvent(
        kind="terminal",
        status=terminal_status,
        payload={
            "event": "run_completed" if terminal_status == "completed" else f"run_{terminal_status}"
        },
    )


async def _deerflow_timeline_events(
    runtime: CodingRuntime,
    *,
    content: str,
    run_id: str,
    surface_context: Mapping[str, Any] | None,
    checkpointer: Any,
    harness_config: HarnessConfig | None = None,
    mcp_catalog: McpCatalogPort | None = None,
    app_env: str = "development",
    resume_value: object | None = None,
    resume_attempt: int = 0,
) -> AsyncGenerator[RunEvent, None]:
    """Run the explicit DeerFlow-compatible graph and project public output."""
    async with runtime.harness_turn(run_id):
        is_resume = resume_value is not None
        prepared = None
        if not is_resume:
            prepared = await runtime.prepare_harness_context(
                user_message=content,
                run_id=run_id,
            )
            runtime.append_harness_message(role="user", content=content, run_id=run_id)
            yield RunEvent(
                kind="user",
                status="completed",
                payload={"type": "user", "content": content, "run_id": run_id},
                event_id=f"harness:{run_id}:user",
            )

        durable_context = build_deerflow_durable_context(runtime)
        context_event = None if is_resume else context_status_event(runtime, run_id, durable_context)
        if prepared is not None:
            for raw_event in prepared.events:
                payload = raw_event.model_dump()
                event_type = str(payload.get("type", ""))
                if event_type == "context_usage_updated" and context_event is not None:
                    continue
                yield RunEvent(
                    kind="context",
                    status=_timeline_status(event_type, payload),
                    payload=payload,
                    event_id=(
                        f"harness:{run_id}:{event_type}:"
                        f"{payload.get('compaction_id', 'context')}"
                    ),
                )
        if context_event is not None:
            yield context_event

        if prepared is not None and not prepared.allow_model_request:
            message = "context emergency: model request blocked"
            yield RunEvent(
                kind="system",
                status="error",
                payload={"type": "error", "run_id": run_id, "message": message},
                event_id=f"harness:{run_id}:context-emergency",
            )
            yield RunEvent(
                kind="terminal",
                status="error",
                payload={
                    "event": "run_error",
                    "runtime_profile": "deerflow_v2",
                    "error_type": "context_emergency",
                },
                event_id=f"harness:{run_id}:terminal",
            )
            return

        mcp_tools: tuple[BaseTool, ...] = ()
        mcp_servers = None
        if isinstance(mcp_catalog, McpManager):
            mcp_scope = McpScope(
                owner_id=runtime.owner_user_id or "local",
                workspace_id=workspace_id_from_path(runtime.workspace.root),
                thread_id=runtime.session_id,
            )
            mcp_snapshot = await mcp_catalog.load_tools(mcp_scope)
            mcp_tools = mcp_snapshot.tools
            mcp_servers = mcp_snapshot.catalog.servers
        if mcp_catalog is not None and not is_resume:
            yield await mcp_catalog_event(
                mcp_catalog,
                session_id=runtime.session_id,
                run_id=run_id,
                servers=mcp_servers,
            )
        workspace_id = workspace_id_from_path(runtime.workspace.root)
        sandbox = create_coding_sandbox(
            runtime.workspace,
            thread_id=runtime.session_id,
            app_env=app_env,
            provider=str(getattr(runtime, "sandbox_provider", "local_workspace")),
            allow_host_shell=True,
            allow_writes=True,
            container_image=str(
                getattr(runtime, "sandbox_image", "python:3.11-slim")
            ),
        )
        try:
            subagent_executor = CodingSubagentExecutor(runtime)
            subagent_config = SubagentToolConfig()
            tool_bundle = build_deerflow_coding_tool_bundle(
                runtime,
                run_id=run_id,
                knowledge_port=CodingKnowledgePort(runtime),
                memory_port=CodingMemoryPort(runtime),
                sandbox=sandbox,
                extra_deferred_tools=mcp_tools,
                subagent_executor=subagent_executor,
                subagent_config=subagent_config,
                graph_approvals=True,
            )
            adapter = SageHarnessRuntimeAdapter(
                model=runtime.model,
                checkpointer=checkpointer,
                tools=tool_bundle.tools,
                system_prompt=build_deerflow_system_prompt(runtime),
                deferred_setup=tool_bundle.deferred_setup,
                skill_catalog=runtime.skill_registry,
                subagent_limits=SubagentLimits(),
                config=harness_config,
                artifact_store=ToolResultStore(
                    runtime.storage_root,
                    runtime.session_id,
                    run_id,
                ),
            )
            graph_compaction: dict[str, object] | None = None
            compaction_result = prepared.compaction_result if prepared is not None else None
            summary_text = durable_context.get("summary_text")
            if (
                compaction_result is not None
                and compaction_result.applied
                and isinstance(summary_text, str)
                and summary_text.strip()
            ):
                graph_compaction = {
                    "compaction_id": compaction_result.compaction_id,
                    "summary_text": summary_text,
                }
            response_parts: list[str] = []
            current_resume_attempt = resume_attempt
            resume = is_resume
            current_resume_value = resume_value
            while True:
                approval_to_resume: str | None = None
                async for event in adapter.stream_turn(
                    session_id=runtime.session_id,
                    run_id=run_id,
                    owner_id=runtime.owner_user_id or "local",
                    workspace_id=workspace_id,
                    workspace_path=str(runtime.workspace.root),
                    content=content,
                    surface_context=surface_context,
                    durable_context=durable_context,
                    graph_compaction=graph_compaction if not resume else None,
                    sandbox=sandbox.descriptor,
                    resume=resume,
                    resume_value=current_resume_value,
                    resume_attempt=current_resume_attempt,
                ):
                    if event.payload.get("type") == "text_delta":
                        response_parts.append(str(event.payload.get("delta", "")))
                    yield event
                    if event.payload.get("type") == "approval_required":
                        approval_to_resume = str(
                            event.payload.get("approval_id", "")
                        ).strip()
                        if not approval_to_resume:
                            raise RuntimeError("graph approval event is missing approval_id")
                if approval_to_resume is None:
                    break
                choice = await runtime.approval_manager.wait_for(
                    runtime.session_id,
                    approval_to_resume,
                )
                if choice is None:
                    raise RuntimeError("graph approval timed out or was cancelled")
                if choice in {"session", "always"}:
                    runtime.approval_manager.consume_resolution(
                        runtime.session_id,
                        approval_to_resume,
                    )
                resume = True
                current_resume_attempt += 1
                current_resume_value = {
                    "approval_id": approval_to_resume,
                    "choice": choice,
                }
        finally:
            await sandbox.aclose()
        answer = "".join(response_parts).strip()
        if not answer:
            raise RuntimeError("deerflow_v2 graph completed without a public assistant response")
        runtime.append_harness_message(role="assistant", content=answer, run_id=run_id)
        yield RunEvent(
            kind="assistant",
            status="completed",
            payload={"type": "final", "content": answer, "run_id": run_id},
            event_id=f"harness:{run_id}:final",
        )
        yield RunEvent(
            kind="terminal",
            status="completed",
            payload={"event": "run_completed", "runtime_profile": "deerflow_v2"},
            event_id=f"harness:{run_id}:terminal",
        )


def _timeline_kind(event_type: str) -> str:
    if event_type in {"final", "text_delta"}:
        return "assistant"
    if event_type.startswith("model_"):
        return "model"
    if event_type.startswith("tool_") or event_type == "workspace_diff_ready":
        return "tool"
    if "approval" in event_type or event_type == "plan_ready_for_review":
        return "approval"
    if event_type.startswith("context_"):
        return "context"
    if event_type.startswith("memory_"):
        return "memory"
    if event_type.startswith("agent_"):
        return "agent"
    if event_type in {"run_finished", "turn_started", "turn_finished"}:
        return "run"
    return "system"


def _timeline_status(event_type: str, event: dict[str, Any]) -> str:
    if event_type in {"approval_required", "plan_ready_for_review"}:
        return "blocked"
    if event_type in {"model_requested", "tool_call", "turn_started"}:
        return "running"
    if event_type in {"error", "context_compaction_failed"}:
        return "error"
    if event_type == "run_finished":
        status = str(event.get("status", "completed"))
        if status == "retryable":
            return "interrupted"
        return status if status in {"completed", "cancelled", "interrupted", "error"} else "completed"
    return "completed"


def _timeline_event_dict(event: SessionEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "session_id": event.session_id,
        "run_id": event.run_id,
        "sequence": event.sequence,
        "kind": event.kind,
        "status": event.status,
        "timestamp": event.timestamp,
        "payload": event.payload,
    }


def _observe_server_task(task: asyncio.Task[None]) -> None:
    """Retrieve a detached task result after Coordinator persisted its terminal."""
    with suppress(asyncio.CancelledError, Exception):
        task.result()


@router.post("/api/v1/coding/session")
async def create_coding_session(
    payload: CodingSessionRequest,
    request: Request,
) -> CodingSessionResponse:
    """Create a coding-agent runtime session."""
    model_factory = getattr(request.app.state, "coding_model_factory", None)
    if model_factory is None:
        raise RuntimeError("Coding model factory is not configured")

    default_workspace = Path(request.app.state.coding_workspace_root).resolve()
    workspace_root = _resolve_workspace_root(default_workspace, payload.workspace_root)
    storage_root = Path(request.app.state.coding_storage_root)
    account = await load_account_model_context(request, include_credentials=True)
    catalog = combined_catalog(request, account)
    model_factory = combined_model_factory(request, account)
    model_id = (
        account.default_model
        if account is not None and account.default_model
        else str(request.app.state.coding_default_model)
    )
    if model_id not in _catalog_model_ids(catalog):
        raise HTTPException(status_code=422, detail="unknown coding model")
    registry = combined_capabilities(request, account)
    reasoning_modes = combined_reasoning_modes(request, account)
    session_id = str(uuid4())
    runtime_profile = _require_enabled_runtime_profile(payload.runtime_profile, request)
    runtime = CodingRuntime(
        session_id=session_id,
        workspace_root=workspace_root,
        model=_build_model(model_factory, model_id, "off"),
        storage_root=storage_root,
        model_factory=model_factory,
        approval_policy=payload.approval_policy,
        save_on_init=True,
        permission_mode="default",
        context_policy=registry.resolve(model_id),
        model_capabilities=registry,
        checkpoint_anchor_key=request.app.state.coding_checkpoint_anchor_key,
        model_spec=model_id,
        reasoning_mode="off",
        model_reasoning_modes=reasoning_modes,
        usage_store=request.app.state.coding_usage_store,
        owner_user_id=account.user_id if account is not None else None,
        knowledge_store=_coding_knowledge_store(request),
        runtime_profile=runtime_profile,
        sandbox_provider=str(
            getattr(request.app.state, "coding_sandbox_provider", "local_workspace")
        ),
        sandbox_image=str(
            getattr(request.app.state, "coding_sandbox_image", "python:3.11-slim")
        ),
    )
    sessions: dict[str, CodingRuntime] = request.app.state.coding_sessions
    sessions[session_id] = runtime
    request.app.state.coding_run_registry.get(session_id)
    return CodingSessionResponse(
        session_id=session_id,
        workspace_root=str(workspace_root.resolve()),
        workspace_id=workspace_id_from_path(workspace_root),
        permission_mode=runtime.permission_mode,
        runtime_profile=runtime.runtime_profile,
        sandbox_provider=runtime.sandbox_provider,
        sandbox_image=runtime.sandbox_image,
    )


@router.get("/api/v1/coding/sessions", response_model=CodingSessionsResponse)
async def list_coding_sessions(
    request: Request,
    include_archived: bool = False,
) -> CodingSessionsResponse:
    """Return local coding-agent session history."""
    storage_root = Path(request.app.state.coding_storage_root)
    store = CodingSessionStore(storage_root / "sessions")
    account = await load_account_model_context(request)
    current_user_id = account.user_id if account is not None else None
    visible: list[CodingSessionSummary] = []
    for item in store.list_sessions(include_archived=include_archived):
        try:
            state = store.load(str(item["session_id"]))
        except FileNotFoundError:
            continue
        owner_user_id = str(state.get("owner_user_id", "")).strip() or None
        if owner_user_id is not None and owner_user_id != current_user_id:
            continue
        visible.append(CodingSessionSummary(**item))
    return CodingSessionsResponse(sessions=visible)


@router.patch(
    "/api/v1/coding/session/{session_id}/metadata",
    response_model=CodingSessionSummary,
)
async def update_coding_session_metadata(
    session_id: str,
    payload: CodingSessionMetadataRequest,
    request: Request,
) -> CodingSessionSummary:
    """Update title/pin/archive metadata without touching runtime history."""
    _require_valid_session_id(session_id)
    if payload.title is None and payload.pinned is None and payload.archived is None:
        raise HTTPException(status_code=422, detail="metadata update is empty")
    store = CodingSessionStore(Path(request.app.state.coding_storage_root) / "sessions")
    try:
        summary = store.update_metadata(
            session_id,
            title=payload.title,
            pinned=payload.pinned,
            archived=payload.archived,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown coding session: {session_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.archived is True:
        await _close_coding_mcp_scope(request, session_id)
    return CodingSessionSummary(**summary)


async def _close_coding_mcp_scope(request: Request, session_id: str) -> None:
    """Best-effort release of stateful MCP subprocesses for an archived chat."""
    manager = getattr(request.app.state, "coding_mcp_manager", None)
    runtime = request.app.state.coding_sessions.get(session_id)
    if not isinstance(manager, McpManager) or not isinstance(runtime, CodingRuntime):
        return
    scope = McpScope(
        owner_id=runtime.owner_user_id or "local",
        workspace_id=workspace_id_from_path(runtime.workspace.root),
        thread_id=runtime.session_id,
    )
    with suppress(Exception):
        await manager.close_scope(scope)


@router.post("/api/v1/coding/session/{session_id}/resume")
async def resume_coding_session(
    session_id: str,
    request: Request,
) -> CodingSessionResponse:
    """Rehydrate a persisted coding runtime session."""
    _require_valid_session_id(session_id)
    model_factory = getattr(request.app.state, "coding_model_factory", None)
    if model_factory is None:
        raise RuntimeError("Coding model factory is not configured")
    storage_root = Path(request.app.state.coding_storage_root)
    store = CodingSessionStore(storage_root / "sessions")
    try:
        persisted = store.load(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown coding session: {session_id}"
        ) from exc
    sessions: dict[str, CodingRuntime] = request.app.state.coding_sessions
    active_runtime = sessions.get(session_id)
    coordinator = await request.app.state.coding_run_registry.hydrate(session_id)
    active_run_id = coordinator.active_run_id or coordinator.journal.active_run_id()
    if active_run_id is not None:
        if active_runtime is None:
            raise HTTPException(
                status_code=409,
                detail="active coding run has no in-memory runtime",
            )
        return CodingSessionResponse(
            session_id=session_id,
            workspace_root=str(active_runtime.workspace.root.resolve()),
            workspace_id=workspace_id_from_path(active_runtime.workspace.root),
            permission_mode=active_runtime.permission_mode,
            runtime_profile=active_runtime.runtime_profile,
            sandbox_provider=active_runtime.sandbox_provider,
            sandbox_image=active_runtime.sandbox_image,
        )
    try:
        runtime_profile = _require_enabled_runtime_profile(persisted.get("runtime_profile"), request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid persisted runtime profile") from exc
    account = await load_account_model_context(request, include_credentials=True)
    catalog = combined_catalog(request, account)
    model_factory = combined_model_factory(request, account)
    registry = combined_capabilities(request, account)
    reasoning_modes = combined_reasoning_modes(request, account)
    model_id = str(persisted.get("model_spec") or request.app.state.coding_default_model)
    if model_id not in _catalog_model_ids(catalog):
        raise HTTPException(status_code=422, detail="unknown coding model")
    default_workspace = Path(request.app.state.coding_workspace_root).resolve()
    persisted_workspace = _resolve_persisted_workspace_root(
        default_workspace, persisted.get("workspace_root")
    )
    persisted["workspace_root"] = str(persisted_workspace)
    reasoning_mode = _resolved_reasoning_mode(
        model_id,
        str(persisted.get("reasoning_mode", "off")),
        reasoning_modes,
    )
    runtime = CodingRuntime(
        session_id=session_id,
        workspace_root=persisted_workspace,
        model=_build_model(model_factory, model_id, reasoning_mode),
        storage_root=storage_root,
        model_factory=model_factory,
        approval_policy="ask",
        session_state=persisted,
        save_on_init=False,
        model_capabilities=registry,
        checkpoint_anchor_key=request.app.state.coding_checkpoint_anchor_key,
        model_spec=model_id,
        reasoning_mode=reasoning_mode,
        model_reasoning_modes=reasoning_modes,
        usage_store=request.app.state.coding_usage_store,
        knowledge_store=_coding_knowledge_store(request),
        runtime_profile=runtime_profile,
        sandbox_provider=str(
            persisted.get(
                "sandbox_provider",
                getattr(request.app.state, "coding_sandbox_provider", "local_workspace"),
            )
        ),
        sandbox_image=str(
            persisted.get(
                "sandbox_image",
                getattr(request.app.state, "coding_sandbox_image", "python:3.11-slim"),
            )
        ),
    )
    sessions[session_id] = runtime
    pending_approval = coordinator.journal.recoverable_approval()
    if pending_approval is not None:
        runtime.approval_manager.restore_pending(pending_approval)
    return CodingSessionResponse(
        session_id=session_id,
        workspace_root=str(persisted_workspace),
        workspace_id=workspace_id_from_path(persisted_workspace),
        permission_mode=runtime.permission_mode,
        runtime_profile=runtime.runtime_profile,
        sandbox_provider=runtime.sandbox_provider,
        sandbox_image=runtime.sandbox_image,
    )


@router.get(
    "/api/v1/coding/session/{session_id}/timeline",
    response_model=CodingTimelineResponse,
)
async def get_coding_session_timeline(
    session_id: str,
    request: Request,
    after: int = 0,
    before: int | None = None,
    tail: bool = False,
    limit: int = 100,
) -> CodingTimelineResponse:
    """Return forward replay or a bounded tail/older browser history page."""
    _require_valid_session_id(session_id)
    if after < 0:
        raise HTTPException(status_code=422, detail="after must be non-negative")
    if before is not None and before <= 0:
        raise HTTPException(status_code=422, detail="before must be positive")
    if (before is not None and after != 0) or (tail and (before is not None or after != 0)):
        raise HTTPException(
            status_code=422,
            detail="after, before, and tail pagination modes are mutually exclusive",
        )
    if not 1 <= limit <= 500:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 500")
    sessions: dict[str, CodingRuntime] = request.app.state.coding_sessions
    if session_id not in sessions:
        store = CodingSessionStore(Path(request.app.state.coding_storage_root) / "sessions")
        try:
            store.load(session_id)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail=f"Unknown coding session: {session_id}"
            ) from exc
    coordinator = await request.app.state.coding_run_registry.hydrate(session_id)
    if tail or before is not None:
        backward_page = coordinator.journal.replay_before(
            before=None if tail else before,
            limit=limit,
        )
        items = backward_page.items
        next_cursor = backward_page.latest_cursor
        has_more = backward_page.has_more
        older_cursor = backward_page.older_cursor
        latest_cursor = backward_page.latest_cursor
    else:
        page = coordinator.journal.replay(after=after, limit=limit)
        items = page.items
        next_cursor = page.next_cursor
        has_more = page.has_more
        older_cursor = None
        latest_cursor = coordinator.journal.latest_sequence()
    active_run_id = coordinator.journal.active_run_id()
    return CodingTimelineResponse(
        items=[
            CodingTimelineEvent(
                event_id=item.event_id,
                session_id=item.session_id,
                run_id=item.run_id,
                sequence=item.sequence,
                kind=item.kind,
                status=item.status,
                timestamp=item.timestamp,
                payload=item.payload,
            )
            for item in items
        ],
        next_cursor=next_cursor,
        has_more=has_more,
        older_cursor=older_cursor,
        latest_cursor=latest_cursor,
        active_run=CodingActiveRun(run_id=active_run_id) if active_run_id else None,
    )


@router.get(
    "/api/v1/coding/session/{session_id}/messages",
    response_model=CodingSessionMessagesResponse,
)
async def get_coding_session_messages(
    session_id: str,
    request: Request,
) -> CodingSessionMessagesResponse:
    """Return persisted chat messages for replaying a coding session."""
    _require_valid_session_id(session_id)
    storage_root = Path(request.app.state.coding_storage_root)
    store = CodingSessionStore(storage_root / "sessions")
    try:
        session_state = store.load(session_id)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown coding session: {session_id}"
        ) from exc
    default_workspace = Path(request.app.state.coding_workspace_root).resolve()
    workspace_root = Path(str(session_state.get("workspace_root", ""))).resolve()
    try:
        workspace_root.relative_to(default_workspace)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="persisted workspace_root must be inside the configured coding workspace",
        ) from exc
    return CodingSessionMessagesResponse(
        messages=[CodingSessionMessage(**item) for item in store.messages(session_id)]
    )


@router.websocket("/api/v1/coding/{session_id}/stream")
async def coding_stream(websocket: WebSocket, session_id: str) -> None:
    """Replay and stream durable events without owning the server run task."""
    await websocket.accept()
    if not _valid_session_id(session_id):
        await websocket.close(code=1008, reason="invalid coding session id")
        return
    sessions: dict[str, CodingRuntime] = websocket.app.state.coding_sessions
    runtime = sessions.get(session_id)
    if runtime is None:
        await websocket.send_json(
            ErrorEvent(message=f"Unknown coding session: {session_id}").model_dump()
        )
        await websocket.close()
        return
    raw_after = websocket.query_params.get("after", "0")
    try:
        after = int(raw_after)
        if after < 0:
            raise ValueError
    except ValueError:
        await websocket.close(code=1008, reason="after must be a non-negative integer")
        return
    coordinator = await websocket.app.state.coding_run_registry.hydrate(session_id)

    async def sender() -> None:
        async for event in coordinator.subscribe(after=after):
            await websocket.send_json(_timeline_event_dict(event))

    async def receiver() -> None:
        while True:
            raw: Any = await websocket.receive_json()
            try:
                message = UserMessage(**raw)
            except Exception as exc:
                await _start_rejected_input_run(
                    coordinator,
                    message=f"Invalid message: {exc}",
                )
                continue
            content = message.content
            surface_context: dict[str, Any] | None = None
            if message.surface_context is not None:
                try:
                    canonical_context = await validate_surface_context(
                        message.surface_context,
                        runtime=runtime,
                        knowledge_store=getattr(websocket.app.state, "knowledge_store", None),
                        knowledge_job_service=getattr(
                            websocket.app.state, "knowledge_job_service", None
                        ),
                        app_env=str(
                            getattr(websocket.app.state, "cloud_app_env", "development")
                        ),
                    )
                except SurfaceContextValidationError as exc:
                    await _start_rejected_input_run(
                        coordinator,
                        message=f"Invalid surface context: {exc}",
                        content=content,
                    )
                    continue
                surface_context = canonical_context.model_dump(mode="json", exclude_none=True)
            expanded, command, args = runtime.resolve_slash(content)
            if command and not expanded:
                await _start_rejected_input_run(
                    coordinator,
                    message=f"Unknown skill: /{command}",
                    content=content,
                )
                continue
            run_id = f"run_{uuid4().hex[:12]}"
            stream = _runtime_timeline_events(
                runtime,
                content=content,
                skill_prompt=expanded or None,
                command=command,
                arguments=args,
                run_id=run_id,
                surface_context=surface_context,
                harness_checkpointer=getattr(websocket.app.state, "sage_harness_checkpointer", None),
                harness_config=getattr(websocket.app.state, "coding_harness_config", None),
                mcp_catalog=getattr(websocket.app.state, "coding_mcp_catalog", None),
                app_env=str(getattr(websocket.app.state, "cloud_app_env", "development")),
            )
            try:
                task = await coordinator.start_run(
                    run_id,
                    stream,
                    surface_context=surface_context,
                )
                task.add_done_callback(_observe_server_task)
            except ActiveRunConflictError:
                await stream.aclose()
                await _start_rejected_input_run(
                    coordinator,
                    message="A run is already in progress for this session",
                    content=content,
                )

    tasks = {asyncio.create_task(sender()), asyncio.create_task(receiver())}
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            with suppress(WebSocketDisconnect, asyncio.CancelledError):
                task.result()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _start_rejected_input_run(
    coordinator: Any, *, message: str, content: str = ""
) -> None:
    run_id = f"run_{uuid4().hex[:12]}"

    def persist() -> None:
        if content:
            coordinator.journal.append(
                run_id=run_id,
                kind="user",
                status="completed",
                payload={"type": "user", "content": content},
            )
        coordinator.journal.append(
            run_id=run_id,
            kind="system",
            status="error",
            payload={"type": "error", "run_id": run_id, "message": message},
        )
        coordinator.journal.append_terminal_once(
            run_id=run_id,
            status="error",
            payload={"event": "input_rejected"},
        )

    await asyncio.to_thread(persist)


@router.get("/api/v1/coding/{session_id}/files", response_model=CodingFilesResponse)
async def list_coding_files(
    session_id: str, request: Request, path: str = "."
) -> CodingFilesResponse:
    """List files in a coding workspace directory."""
    runtime = _require_runtime(request, session_id)
    try:
        entries = runtime.list_files(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CodingFilesResponse(
        path=path,
        entries=[CodingFileEntry(**entry) for entry in entries],
    )


@router.get("/api/v1/coding/{session_id}/file", response_model=CodingFileContentResponse)
async def read_coding_file(
    session_id: str, path: str, request: Request
) -> CodingFileContentResponse:
    """Read a file from a coding workspace."""
    runtime = _require_runtime(request, session_id)
    try:
        result = runtime.read_file(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CodingFileContentResponse(**result)


@router.get("/api/v1/coding/{session_id}/git/status", response_model=CodingGitStatusResponse)
async def coding_git_status(session_id: str, request: Request) -> CodingGitStatusResponse:
    """Return git branch and dirty file count for the workspace."""
    runtime = _require_runtime(request, session_id)
    return CodingGitStatusResponse(**runtime.git_status())


@router.get(
    "/api/v1/coding/{session_id}/context",
    response_model=CodingContextSnapshot,
)
async def coding_context_snapshot(
    session_id: str, request: Request
) -> CodingContextSnapshot:
    """Return the session's server-owned context budget and checkpoint status."""
    runtime = _require_runtime(request, session_id)
    return CodingContextSnapshot(**runtime.context_snapshot())


@router.post(
    "/api/v1/coding/{session_id}/context/compact",
    response_model=CodingContextCompactResponse,
)
async def compact_coding_context(
    session_id: str,
    payload: CodingContextCompactRequest,
    request: Request,
) -> CodingContextCompactResponse:
    """Compact context explicitly; the runtime persists terminal evidence first."""
    runtime = _require_runtime(request, session_id)
    if runtime.context_controller is None:
        raise HTTPException(status_code=422, detail="context window is not configured")
    if _coding_operation_busy(request, session_id, runtime):
        raise HTTPException(status_code=409, detail="context operation is busy")
    try:
        result = await runtime.manual_compact(payload.focus)
    except ContextBusyError as exc:
        raise HTTPException(status_code=409, detail="context operation is busy") from exc
    except ValueError as exc:
        if str(exc) == "context window is not configured":
            raise HTTPException(
                status_code=422, detail="context window is not configured"
            ) from exc
        raise HTTPException(status_code=500, detail="context compaction failed") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="context compaction failed") from exc
    try:
        artifact = runtime.compaction_store.load(session_id, result.compaction_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="context compaction failed") from exc
    if artifact is None or artifact.get("status") not in {"completed", "failed"}:
        raise HTTPException(status_code=500, detail="context compaction failed")
    return CodingContextCompactResponse(
        compaction_id=result.compaction_id,
        applied=result.applied,
        before_tokens=result.before_tokens,
        after_tokens=result.after_tokens,
        archived_items=result.archived_items,
        reason=result.reason,
        retryable=result.retryable,
        context=CodingContextSnapshot(**runtime.context_snapshot()),
    )


@router.get(
    "/api/v1/coding/{session_id}/approval/pending",
    response_model=CodingApprovalResponse | None,
)
async def coding_pending_approval(
    session_id: str,
    request: Request,
) -> CodingApprovalResponse | None:
    """Return the oldest pending approval for a coding session."""
    runtime = _require_runtime(request, session_id)
    pending = runtime.approval_manager.pending(session_id)
    return CodingApprovalResponse(**pending) if pending else None


@router.post("/api/v1/coding/{session_id}/approval/respond")
async def coding_approval_respond(
    session_id: str,
    payload: CodingApprovalRespondRequest,
    request: Request,
) -> dict[str, bool]:
    """Resolve a pending tool approval and resume a recovered graph run."""
    runtime = _require_runtime(request, session_id)
    coordinator = request.app.state.coding_run_registry.get(session_id)
    run_id = runtime.approval_manager.run_id_for(session_id, payload.approval_id)
    resume_plan: tuple[str, str, dict[str, Any], int] | None = None
    durable_approval: dict[str, Any] | None = None
    if (
        run_id is not None
        and coordinator.active_run_id is None
        and runtime.runtime_profile == "deerflow_v2"
    ):
        durable_approval = coordinator.journal.recoverable_approval(run_id)
        if (
            durable_approval is None
            or durable_approval.get("approval_id") != payload.approval_id
        ):
            raise HTTPException(
                status_code=409,
                detail="Durable approval checkpoint is unavailable",
            )
        resume_context = coordinator.journal.run_resume_context(run_id)
        if resume_context is None:
            raise HTTPException(
                status_code=409,
                detail="Durable approval run context is unavailable",
            )
        content, surface_context = resume_context
        resume_plan = (
            run_id,
            content,
            surface_context,
            max(1, int(durable_approval.get("resume_attempt", 1))),
        )
    ok = runtime.approval_manager.resolve(session_id, payload.approval_id, payload.choice)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Unknown approval: {payload.approval_id}")
    if resume_plan is not None:
        resolved_choice = await runtime.approval_manager.wait_for(
            session_id,
            payload.approval_id,
        )
        if resolved_choice is None:
            raise HTTPException(
                status_code=409,
                detail="Approval resolution is unavailable",
            )
        resumed_run_id, content, surface_context, resume_attempt = resume_plan
        stream = _runtime_timeline_events(
            runtime,
            content=content,
            skill_prompt=None,
            command="",
            arguments="",
            run_id=resumed_run_id,
            surface_context=surface_context,
            harness_checkpointer=getattr(
                request.app.state,
                "sage_harness_checkpointer",
                None,
            ),
            harness_config=getattr(request.app.state, "coding_harness_config", None),
            mcp_catalog=getattr(request.app.state, "coding_mcp_catalog", None),
            app_env=str(getattr(request.app.state, "cloud_app_env", "development")),
            resume_value={
                "approval_id": payload.approval_id,
                "choice": resolved_choice,
            },
            resume_attempt=resume_attempt,
        )
        try:
            task = await coordinator.start_existing_run(resumed_run_id, stream)
        except ActiveRunConflictError:
            await stream.aclose()
            if coordinator.active_run_id != resumed_run_id:
                raise HTTPException(
                    status_code=409,
                    detail="Another coding run became active",
                ) from None
        except Exception:
            runtime.approval_manager.consume_resolution(
                session_id,
                payload.approval_id,
            )
            if durable_approval is not None:
                runtime.approval_manager.restore_pending(durable_approval)
            raise
        else:
            task.add_done_callback(_observe_server_task)
    return {"ok": True}


@router.post("/api/v1/coding/{session_id}/run/stop")
async def stop_coding_run(
    session_id: str, payload: CodingRunStopRequest, request: Request
) -> dict[str, bool]:
    """Cancel only the explicitly identified active coding run."""
    runtime = _require_runtime(request, session_id)
    coordinator = request.app.state.coding_run_registry.get(session_id)
    if coordinator.active_run_id != payload.run_id:
        return {"ok": False}
    child_run_ids = await asyncio.to_thread(
        coordinator.journal.active_subagent_run_ids,
        payload.run_id,
    )
    audit_events = tuple(
        RunEvent(
            kind="agent",
            status="error",
            payload={
                "type": "subagent_cancelled",
                "session_id": session_id,
                "run_id": payload.run_id,
                "parent_run_id": payload.run_id,
                "child_run_id": child_run_id,
                "agent_run_id": child_run_id,
                "status": "cancelled",
                "error_code": "parent_cancelled",
            },
            event_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"sage://coding/{session_id}/{payload.run_id}/cancel/{child_run_id}",
                )
            ),
        )
        for child_run_id in child_run_ids
    )
    if runtime.active_run_id == payload.run_id:
        runtime.request_stop(run_id=payload.run_id)
    return {
        "ok": await coordinator.cancel(
            payload.run_id,
            audit_events=audit_events,
        )
    }


@router.post("/api/v1/coding/{session_id}/plan/approve")
async def approve_plan(session_id: str, request: Request) -> dict[str, str]:
    """Approve the pending plan review and exit plan mode."""
    runtime = _require_runtime(request, session_id)
    if runtime.runtime_mode != "plan":
        raise HTTPException(status_code=400, detail="not in plan mode")
    success = runtime.approve_plan()
    if not success:
        raise HTTPException(status_code=400, detail="no pending plan review")
    return {"status": "approved", "mode": runtime.runtime_mode}


@router.post("/api/v1/coding/{session_id}/plan/reject")
async def reject_plan(session_id: str, request: Request) -> dict[str, str]:
    """Reject the pending plan review, staying in plan mode."""
    runtime = _require_runtime(request, session_id)
    if runtime.runtime_mode != "plan":
        raise HTTPException(status_code=400, detail="not in plan mode")
    success = runtime.reject_plan()
    if not success:
        raise HTTPException(status_code=400, detail="no pending plan review")
    return {"status": "rejected", "mode": runtime.runtime_mode}


@router.get(
    "/api/v1/coding/{session_id}/memory/proposals",
    response_model=CodingMemoryProposalsResponse,
)
async def list_memory_proposals(
    session_id: str,
    request: Request,
    status: Literal["pending", "approved", "rejected"] | None = None,
) -> CodingMemoryProposalsResponse:
    """List only proposals owned by this coding session."""
    runtime = _require_runtime(request, session_id)
    try:
        proposals = [
            proposal
            for proposal in runtime.memory_manager.list_proposals(status)
            if proposal.session_id == session_id
        ]
    except MemoryStoreError as exc:
        raise _memory_storage_error() from exc
    return CodingMemoryProposalsResponse(
        proposals=[_memory_proposal_response(proposal) for proposal in proposals]
    )


@router.get(
    "/api/v1/coding/{session_id}/memory/proposals/{proposal_id}",
    response_model=CodingMemoryProposalDetail,
)
async def get_memory_proposal(
    session_id: str, proposal_id: str, request: Request
) -> CodingMemoryProposalDetail:
    """Return a session-owned proposal and its durable event trail."""
    runtime = _require_runtime(request, session_id)
    proposal = _session_memory_proposal(runtime, session_id, proposal_id)
    try:
        events = runtime.memory_manager.list_memory_events(proposal_id)
    except MemoryStoreError as exc:
        raise _memory_storage_error() from exc
    return CodingMemoryProposalDetail(
        proposal=_memory_proposal_response(proposal),
        events=[_memory_event_response(event) for event in events],
    )


@router.post(
    "/api/v1/coding/{session_id}/memory/proposals/{proposal_id}/approve",
    response_model=CodingMemoryProposal,
)
async def approve_memory_proposal_by_id(
    session_id: str,
    proposal_id: str,
    payload: CodingMemoryProposalTransitionRequest,
    request: Request,
) -> CodingMemoryProposal:
    """Approve one persisted proposal with optimistic revision control."""
    return _transition_memory_proposal(
        request, session_id, proposal_id, payload.expected_revision, "approved"
    )


@router.post(
    "/api/v1/coding/{session_id}/memory/proposals/{proposal_id}/reject",
    response_model=CodingMemoryProposal,
)
async def reject_memory_proposal_by_id(
    session_id: str,
    proposal_id: str,
    payload: CodingMemoryProposalTransitionRequest,
    request: Request,
) -> CodingMemoryProposal:
    """Reject one persisted proposal with optimistic revision control."""
    return _transition_memory_proposal(
        request, session_id, proposal_id, payload.expected_revision, "rejected"
    )


@router.post(
    "/api/v1/coding/{session_id}/memory/proposal/approve",
    response_model=CodingMemoryProposal,
    deprecated=True,
)
async def approve_memory_proposal(
    session_id: str,
    payload: CodingMemoryProposalDecisionRequest,
    request: Request,
    response: Response,
) -> CodingMemoryProposal:
    """Deprecated compatibility route; still requires ID plus revision CAS."""
    response.headers["Deprecation"] = "true"
    return _transition_memory_proposal(
        request, session_id, payload.proposal_id, payload.expected_revision, "approved"
    )


@router.post(
    "/api/v1/coding/{session_id}/memory/proposal/reject",
    response_model=CodingMemoryProposal,
    deprecated=True,
)
async def reject_memory_proposal(
    session_id: str,
    payload: CodingMemoryProposalDecisionRequest,
    request: Request,
    response: Response,
) -> CodingMemoryProposal:
    """Deprecated compatibility route; still requires ID plus revision CAS."""
    response.headers["Deprecation"] = "true"
    return _transition_memory_proposal(
        request, session_id, payload.proposal_id, payload.expected_revision, "rejected"
    )


@router.get("/api/v1/coding/{session_id}/runs", response_model=CodingRunsResponse)
async def list_coding_runs(session_id: str, request: Request) -> CodingRunsResponse:
    """Return persisted run summaries for a coding session."""
    runtime = _require_runtime(request, session_id)
    return CodingRunsResponse(
        runs=[CodingRunSummary(**item) for item in runtime.run_store.list_runs()]
    )


@router.get(
    "/api/v1/coding/{session_id}/runs/{run_id}",
    response_model=CodingRunDetailResponse,
)
async def get_coding_run(
    session_id: str,
    run_id: str,
    request: Request,
) -> CodingRunDetailResponse:
    """Return one persisted run trace."""
    runtime = _require_runtime(request, session_id)
    try:
        return CodingRunDetailResponse(**runtime.run_store.get_run(run_id))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}") from exc


@router.get("/api/v1/coding/{session_id}/runs/{run_id}/diff")
async def get_coding_run_diff(
    session_id: str, run_id: str, request: Request
) -> dict[str, Any]:
    """Return the workspace diff artifact for a completed run."""
    runtime = _require_runtime(request, session_id)
    diff_path = runtime.run_store.evidence_root / run_id / "diff.json"
    if not diff_path.is_file():
        raise HTTPException(
            status_code=404, detail="diff not found for this run"
        )
    import json

    return cast(dict[str, Any], json.loads(diff_path.read_text(encoding="utf-8")))


@router.get("/api/v1/coding/models", response_model=CodingModelsResponse)
async def list_coding_models(
    request: Request, session_id: str | None = None
) -> CodingModelsResponse:
    """Return the server whitelist and only explicitly configured capabilities."""
    account = await load_account_model_context(request)
    catalog = combined_catalog(request, account)
    registry = combined_capabilities(request, account)
    models: list[CodingModel] = []
    for item in catalog:
        model_id = str(item["id"])
        policy = registry.resolve(model_id)
        models.append(
            CodingModel(
                id=model_id,
                label=str(item["label"]),
                provider=str(item["provider"]),
                context_configured=policy is not None,
                context_window_tokens=(policy.context_window_tokens if policy else None),
                output_reserve_tokens=(policy.output_reserve_tokens if policy else None),
                reasoning_modes=[str(mode) for mode in item.get("reasoning_modes", [])],
            )
        )
    current = (
        account.default_model
        if account is not None and account.default_model
        else str(request.app.state.coding_default_model)
    )
    reasoning_mode = "off"
    if session_id is not None:
        runtime = _require_runtime(request, session_id)
        current = runtime.model_spec
        reasoning_mode = runtime.reasoning_mode
    elif len(request.app.state.coding_sessions) == 1:
        runtime = next(iter(request.app.state.coding_sessions.values()))
        current_user_id = account.user_id if account is not None else None
        if runtime.owner_user_id is None or runtime.owner_user_id == current_user_id:
            current = runtime.model_spec
            reasoning_mode = runtime.reasoning_mode
    return CodingModelsResponse(
        models=models,
        current=current,
        reasoning_mode=cast(Literal["off", "low", "medium", "high"], reasoning_mode),
        runtime_profiles=_available_runtime_profiles(request),
    )


@router.patch("/api/v1/coding/{session_id}/model")
async def switch_coding_model(
    session_id: str,
    payload: CodingModelSwitchRequest,
    request: Request,
) -> dict[str, Any]:
    """Switch the model used by a coding session."""
    runtime = _require_runtime(request, session_id)
    account = await load_account_model_context(request, include_credentials=True)
    if account is not None:
        try:
            runtime.bind_owner(account.user_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=404, detail=f"Unknown coding session: {session_id}"
            ) from exc
    catalog = combined_catalog(request, account)
    allowed = _catalog_model_ids(catalog)
    if payload.model_id not in allowed:
        raise HTTPException(status_code=422, detail="unknown coding model")
    if _coding_operation_busy(request, session_id, runtime):
        raise HTTPException(status_code=409, detail="context operation is busy")
    model_factory = combined_model_factory(request, account)
    runtime.model_capabilities = combined_capabilities(request, account)
    runtime.model_reasoning_modes = combined_reasoning_modes(request, account)

    def factory(
        model_id: str = payload.model_id, *, reasoning_mode: str = "off"
    ) -> Any:
        return _build_model(model_factory, model_id, reasoning_mode)

    try:
        runtime.switch_model(payload.model_id, factory)
    except ContextBusyError as exc:
        raise HTTPException(status_code=409, detail="context operation is busy") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="model switch failed") from exc
    return {
        "ok": True,
        "model_id": payload.model_id,
        "reasoning_mode": runtime.reasoning_mode,
    }


@router.patch("/api/v1/coding/{session_id}/reasoning")
async def switch_coding_reasoning(
    session_id: str,
    payload: CodingReasoningSwitchRequest,
    request: Request,
) -> dict[str, Any]:
    """Select a reasoning mode only when the active model declares it."""
    runtime = _require_runtime(request, session_id)
    if _coding_operation_busy(request, session_id, runtime):
        raise HTTPException(status_code=409, detail="context operation is busy")
    try:
        runtime.switch_reasoning(payload.mode)
    except ContextBusyError as exc:
        raise HTTPException(status_code=409, detail="context operation is busy") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "model_id": runtime.model_spec, "reasoning_mode": runtime.reasoning_mode}


@router.get(
    "/api/v1/coding/providers",
    response_model=CodingProviderSettingsResponse,
)
async def get_coding_provider_settings(request: Request) -> CodingProviderSettingsResponse:
    """Return only non-secret, project-local provider configuration."""
    settings = _provider_settings(request)
    store = cast(SageProviderSettingsStore, request.app.state.coding_provider_settings_store)
    return _provider_settings_response(settings, source=store.source, editable=store.editable)


@router.put(
    "/api/v1/coding/providers",
    response_model=CodingProviderSettingsResponse,
)
async def update_coding_provider_settings(
    payload: dict[str, Any],
    request: Request,
) -> CodingProviderSettingsResponse:
    """Atomically replace the non-secret settings document for this workspace."""
    _provider_settings(request)
    store = cast(SageProviderSettingsStore, request.app.state.coding_provider_settings_store)
    try:
        # The core parser owns strict validation so FastAPI never reflects an
        # unknown field's submitted value (which could be an accidental key).
        settings = store.save(payload)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="provider settings are deployment managed") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    request.app.state.coding_provider_settings = settings
    request.app.state.coding_default_model = settings.default_model
    request.app.state.coding_model_catalog = settings.catalog
    request.app.state.coding_model_capabilities = settings.registry
    request.app.state.coding_model_reasoning_modes = settings.reasoning_modes
    for runtime in request.app.state.coding_sessions.values():
        if runtime.active_run_id is not None or runtime._context_operation_lock.locked():
            continue
        runtime.model_capabilities = settings.registry
        runtime.model_reasoning_modes = request.app.state.coding_model_reasoning_modes
    return _provider_settings_response(settings, source=store.source, editable=store.editable)


@router.get("/api/v1/coding/usage", response_model=CodingUsageSummary)
async def get_coding_usage(
    request: Request,
    range: Literal["7d", "30d", "90d", "365d"] = Query(default="30d"),
) -> CodingUsageSummary:
    """Aggregate Provider-reported token metadata from the local ledger."""
    days = {"7d": 7, "30d": 30, "90d": 90, "365d": 365}[range]
    return CodingUsageSummary(**request.app.state.coding_usage_store.summary(days=days))


@router.patch("/api/v1/coding/{session_id}/permission-mode")
async def switch_permission_mode(
    session_id: str, payload: PermissionModeSwitchRequest, request: Request
) -> dict[str, Any]:
    """Switch the active permission mode at runtime."""
    runtime = _require_runtime(request, session_id)
    runtime.set_permission_mode(payload.mode)
    return {"ok": True, "mode": payload.mode}


@router.get("/api/v1/coding/skills", response_model=CodingSkillsResponse)
async def list_coding_skills(request: Request) -> CodingSkillsResponse:
    """List all available coding skills (uses a fresh registry from repo root)."""
    repo_root = Path(request.app.state.coding_workspace_root).resolve()
    from core.coding.skills import SkillRegistry

    registry = SkillRegistry(root=repo_root)
    skills = [
        CodingSkillSummary(
            name=skill.name,
            description=skill.description,
            source=skill.source,
            argument_hint=skill.argument_hint,
        )
        for skill in registry.list()
    ]
    return CodingSkillsResponse(skills=skills)


@router.get("/api/v1/coding/skills/{name}", response_model=CodingSkillDetailResponse)
async def get_coding_skill(name: str, request: Request) -> CodingSkillDetailResponse:
    """Return one skill's content."""
    repo_root = Path(request.app.state.coding_workspace_root).resolve()
    from core.coding.skills import SkillRegistry

    registry = SkillRegistry(root=repo_root)
    skill = registry.get(name)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Unknown skill: {name}")
    return CodingSkillDetailResponse(
        name=skill.name,
        description=skill.description,
        source=skill.source,
        content=skill.prompt,
    )


@router.get("/api/v1/coding/mcp/servers", response_model=CodingMcpServersResponse)
async def list_coding_mcp_servers(request: Request) -> CodingMcpServersResponse:
    """Return MCP server config (read-only, no live connection check)."""
    catalog: McpCatalogPort = request.app.state.coding_mcp_catalog
    configured = await catalog.list_servers()
    servers = [
        CodingMcpServer(name=server.name, transport=server.transport, status=server.status)
        for server in configured
    ]
    return CodingMcpServersResponse(servers=servers)


def _require_runtime(request: Request, session_id: str) -> CodingRuntime:
    sessions: dict[str, CodingRuntime] = request.app.state.coding_sessions
    runtime = sessions.get(session_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"Unknown coding session: {session_id}")
    return runtime


def _coding_operation_busy(
    request: Request, session_id: str, runtime: CodingRuntime
) -> bool:
    coordinator = request.app.state.coding_run_registry.get(session_id)
    return (
        coordinator.journal.active_run_id() is not None
        or runtime.active_run_id is not None
        or runtime._context_operation_lock.locked()
    )


def _session_memory_proposal(
    runtime: CodingRuntime, session_id: str, proposal_id: str
) -> MemoryProposal:
    """Resolve a proposal without exposing another session or workspace."""
    if not proposal_id or len(proposal_id) > 128:
        raise HTTPException(status_code=404, detail="memory proposal not found")
    try:
        proposal = runtime.memory_manager.get_proposal(proposal_id)
    except MemoryStoreError as exc:
        raise _memory_storage_error() from exc
    if proposal is None or proposal.session_id != session_id:
        raise HTTPException(status_code=404, detail="memory proposal not found")
    return proposal


def _transition_memory_proposal(
    request: Request,
    session_id: str,
    proposal_id: str,
    expected_revision: int,
    status: Literal["approved", "rejected"],
) -> CodingMemoryProposal:
    runtime = _require_runtime(request, session_id)
    _session_memory_proposal(runtime, session_id, proposal_id)
    try:
        proposal = (
            runtime.memory_manager.approve(proposal_id, expected_revision)
            if status == "approved"
            else runtime.memory_manager.reject(proposal_id, expected_revision)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="memory proposal not found") from exc
    except MemoryConflictError as exc:
        raise HTTPException(
            status_code=409, detail="memory proposal conflict"
        ) from exc
    except (MemoryStoreError, OSError) as exc:
        raise _memory_storage_error() from exc
    return _memory_proposal_response(proposal)


def _memory_proposal_response(proposal: MemoryProposal) -> CodingMemoryProposal:
    return CodingMemoryProposal(
        proposal_id=proposal.proposal_id,
        workspace_id=proposal.workspace_id,
        session_id=proposal.session_id,
        run_id=proposal.run_id,
        reflection_id=proposal.reflection_id,
        status=cast(Any, proposal.status),
        projection_status=cast(Any, proposal.projection_status),
        revision=proposal.revision,
        base_revision=proposal.base_revision,
        candidate_count=len(proposal.candidates),
        candidates=[
            CodingMemoryCandidate(
                content=candidate.content,
                topic=candidate.topic,
                source=candidate.source,
                source_ref=candidate.source_ref,
                created_at=candidate.created_at,
            )
            for candidate in proposal.candidates
        ],
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
    )


def _memory_event_response(event: MemoryEvent) -> CodingMemoryEvent:
    return CodingMemoryEvent(**event.__dict__)


def _memory_storage_error() -> HTTPException:
    return HTTPException(status_code=500, detail="memory proposal operation failed")


def _resolve_workspace_root(default_workspace: Path, override: str | None) -> Path:
    """Resolve a requested workspace and keep it inside the configured root."""
    if not override:
        return default_workspace
    workspace_root = Path(override).expanduser().resolve()
    try:
        workspace_root.relative_to(default_workspace)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="workspace_root must be inside the configured coding workspace",
        ) from exc
    return workspace_root


def _build_model(factory: Any, model_id: str, reasoning_mode: str = "off") -> Any:
    """Call legacy and reasoning-aware factories without swallowing their failures."""
    try:
        factory_signature = signature(factory)
    except (TypeError, ValueError):
        return factory(model_id, reasoning_mode=reasoning_mode)
    for args, kwargs in (
        ((model_id,), {"reasoning_mode": reasoning_mode}),
        ((model_id,), {}),
        ((), {}),
    ):
        try:
            factory_signature.bind(*args, **kwargs)
        except TypeError:
            continue
        return factory(*args, **kwargs)
    return factory()


def _catalog_model_ids(catalog: list[dict[str, Any]]) -> set[str]:
    return {str(item["id"]) for item in catalog}


def _resolved_reasoning_mode(
    model_id: str,
    mode: str,
    reasoning_modes: Mapping[str, tuple[str, ...] | list[str]],
) -> str:
    if mode == "off":
        return "off"
    available = reasoning_modes.get(model_id, ())
    return mode if mode in available else "off"


def _provider_settings(request: Request) -> SageProviderSettings:
    settings = getattr(request.app.state, "coding_provider_settings", None)
    if not isinstance(settings, SageProviderSettings):
        raise HTTPException(status_code=503, detail="provider settings are unavailable")
    return settings


def _provider_settings_response(
    settings: SageProviderSettings,
    *,
    source: str,
    editable: bool,
) -> CodingProviderSettingsResponse:
    providers = [
        CodingProviderResponse(
            id=provider.id,
            label=provider.label,
            api_mode=provider.api_mode,
            base_url=provider.base_url,
            api_key_env=provider.api_key_env,
            api_key_configured=bool(os.environ.get(provider.api_key_env, "").strip()),
            models=[
                CodingProviderModelResponse(
                    id=model.id,
                    label=model.label,
                    context_window_tokens=model.context_window_tokens,
                    output_reserve_tokens=model.output_reserve_tokens,
                    reasoning=model.reasoning.to_mapping(),
                )
                for model in provider.models
            ],
        )
        for provider in settings.providers
    ]
    return CodingProviderSettingsResponse(
        version=1,
        default_model=settings.default_model,
        source=cast(Literal["legacy_toml", "project_json", "deployment_json"], source),
        editable=editable,
        providers=providers,
    )


def _resolve_persisted_workspace_root(
    default_workspace: Path, raw_workspace: object
) -> Path:
    if not isinstance(raw_workspace, str) or not raw_workspace.strip():
        raise HTTPException(status_code=400, detail="persisted workspace_root is invalid")
    workspace = Path(raw_workspace).expanduser().resolve()
    try:
        workspace.relative_to(default_workspace)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="persisted workspace_root must be inside the configured coding workspace",
        ) from exc
    return workspace
