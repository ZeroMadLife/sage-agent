"""Coding-agent REST and WebSocket routes."""

from __future__ import annotations

from inspect import signature
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response, WebSocket

from api.schemas import (
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
    CodingRunDetailResponse,
    CodingRunsResponse,
    CodingRunStopRequest,
    CodingRunSummary,
    CodingSessionMessage,
    CodingSessionMessagesResponse,
    CodingSessionRequest,
    CodingSessionResponse,
    CodingSessionsResponse,
    CodingSessionSummary,
    CodingSkillDetailResponse,
    CodingSkillsResponse,
    CodingSkillSummary,
    ErrorEvent,
    PermissionModeSwitchRequest,
    UserMessage,
)
from core.coding.context import ContextBusyError, ModelCapabilityRegistry
from core.coding.persistence import (
    CodingSessionStore,
    MemoryConflictError,
    MemoryEvent,
    MemoryProposal,
    MemoryStoreError,
)
from core.coding.runtime import CodingRuntime

router = APIRouter()


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
    model_id = str(request.app.state.coding_default_model)
    if model_id not in _catalog_model_ids(request):
        raise HTTPException(status_code=422, detail="unknown coding model")
    registry: ModelCapabilityRegistry = request.app.state.coding_model_capabilities
    session_id = str(uuid4())
    runtime = CodingRuntime(
        session_id=session_id,
        workspace_root=workspace_root,
        model=_build_model(model_factory, model_id),
        storage_root=storage_root,
        model_factory=model_factory,
        approval_policy=payload.approval_policy,
        save_on_init=False,
        permission_mode="default",
        context_policy=registry.resolve(model_id),
        model_capabilities=registry,
        checkpoint_anchor_key=request.app.state.coding_checkpoint_anchor_key,
        model_spec=model_id,
    )
    sessions: dict[str, CodingRuntime] = request.app.state.coding_sessions
    sessions[session_id] = runtime
    return CodingSessionResponse(
        session_id=session_id,
        workspace_root=str(workspace_root.resolve()),
        permission_mode=runtime.permission_mode,
    )


@router.get("/api/v1/coding/sessions", response_model=CodingSessionsResponse)
async def list_coding_sessions(request: Request) -> CodingSessionsResponse:
    """Return local coding-agent session history."""
    storage_root = Path(request.app.state.coding_storage_root)
    store = CodingSessionStore(storage_root / "sessions")
    return CodingSessionsResponse(
        sessions=[CodingSessionSummary(**item) for item in store.list_sessions()]
    )


@router.post("/api/v1/coding/session/{session_id}/resume")
async def resume_coding_session(
    session_id: str,
    request: Request,
) -> CodingSessionResponse:
    """Rehydrate a persisted coding runtime session."""
    model_factory = getattr(request.app.state, "coding_model_factory", None)
    if model_factory is None:
        raise RuntimeError("Coding model factory is not configured")
    storage_root = Path(request.app.state.coding_storage_root)
    registry: ModelCapabilityRegistry = request.app.state.coding_model_capabilities
    store = CodingSessionStore(storage_root / "sessions")
    try:
        persisted = store.load(session_id)
        model_id = str(
            persisted.get("model_spec") or request.app.state.coding_default_model
        )
        if model_id not in _catalog_model_ids(request):
            raise HTTPException(status_code=422, detail="unknown coding model")
        default_workspace = Path(request.app.state.coding_workspace_root).resolve()
        persisted_workspace = _resolve_persisted_workspace_root(
            default_workspace, persisted.get("workspace_root")
        )
        persisted["workspace_root"] = str(persisted_workspace)
        runtime = CodingRuntime(
            session_id=session_id,
            workspace_root=persisted_workspace,
            model=_build_model(model_factory, model_id),
            storage_root=storage_root,
            model_factory=model_factory,
            approval_policy="ask",
            session_state=persisted,
            save_on_init=False,
            model_capabilities=registry,
            checkpoint_anchor_key=request.app.state.coding_checkpoint_anchor_key,
            model_spec=model_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown coding session: {session_id}"
        ) from exc
    sessions: dict[str, CodingRuntime] = request.app.state.coding_sessions
    sessions[session_id] = runtime
    return CodingSessionResponse(
        session_id=session_id,
        workspace_root=str(persisted_workspace),
        permission_mode=runtime.permission_mode,
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
    """Stream coding-agent events over WebSocket."""
    await websocket.accept()
    sessions: dict[str, CodingRuntime] = websocket.app.state.coding_sessions
    runtime = sessions.get(session_id)
    if runtime is None:
        await websocket.send_json(
            ErrorEvent(message=f"Unknown coding session: {session_id}").model_dump()
        )
        await websocket.close()
        return

    # Sync the runtime mode on (re)connect so the frontend knows whether the
    # session is currently in plan mode. This only fires when a turn is not
    # actively streaming, since mode changes mid-turn are delivered via the
    # runtime_mode_changed events yielded by run_turn().
    if runtime.runtime_mode != "default":
        await websocket.send_json(
            {
                "type": "runtime_mode_changed",
                "run_id": "",
                "mode": runtime.runtime_mode,
                "topic": runtime.plan_mode.topic,
                "plan_path": runtime.plan_mode.plan_path,
            }
        )

    # Re-surface an outstanding plan review on (re)connect so the frontend can
    # render the approval UI even if the plan_ready_for_review event was missed
    # (e.g. the turn already finished streaming before the client connected).
    pending_review = runtime.plan_review_manager.pending
    if pending_review is not None and not pending_review.event.is_set():
        await websocket.send_json(
            {
                "type": "plan_ready_for_review",
                "run_id": "",
                "review_id": pending_review.review_id,
                "plan_path": pending_review.plan_path,
                "summary": pending_review.summary,
            }
        )

    while True:
        try:
            raw: Any = await websocket.receive_json()
        except Exception:
            break
        try:
            message = UserMessage(**raw)
        except Exception as exc:
            await websocket.send_json(ErrorEvent(message=f"Invalid message: {exc}").model_dump())
            continue

        content = message.content
        expanded, command, args = runtime.resolve_slash(content)
        if command and not expanded:
            await websocket.send_json(ErrorEvent(message=f"Unknown skill: /{command}").model_dump())
            continue
        if expanded:
            await websocket.send_json(
                {"type": "skill_invoked", "skill": command, "arguments": args}
            )
            # Pass original user text to run_turn; the expanded skill prompt is
            # injected into the LLM request only and is not persisted to history.
            skill_prompt = expanded
        else:
            skill_prompt = None

        try:
            async for event in runtime.run_turn(content, skill_prompt=skill_prompt):
                await websocket.send_json(event)
        except Exception as exc:
            await websocket.send_json(ErrorEvent(message=f"Coding agent error: {exc}").model_dump())


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
    if runtime.active_run_id is not None or runtime._context_operation_lock.locked():
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
    """Resolve a pending tool approval."""
    runtime = _require_runtime(request, session_id)
    ok = runtime.approval_manager.resolve(session_id, payload.approval_id, payload.choice)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Unknown approval: {payload.approval_id}")
    return {"ok": True}


@router.post("/api/v1/coding/{session_id}/run/stop")
async def stop_coding_run(
    session_id: str, payload: CodingRunStopRequest, request: Request
) -> dict[str, bool]:
    """Cancel only the explicitly identified active coding run."""
    runtime = _require_runtime(request, session_id)
    stopped = runtime.request_stop(run_id=payload.run_id)
    return {"ok": stopped}


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
    registry: ModelCapabilityRegistry = request.app.state.coding_model_capabilities
    models: list[CodingModel] = []
    for item in request.app.state.coding_model_catalog:
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
            )
        )
    current = str(request.app.state.coding_default_model)
    if session_id is not None:
        current = _require_runtime(request, session_id).model_spec
    elif len(request.app.state.coding_sessions) == 1:
        current = next(iter(request.app.state.coding_sessions.values())).model_spec
    return CodingModelsResponse(models=models, current=current)


@router.patch("/api/v1/coding/{session_id}/model")
async def switch_coding_model(
    session_id: str,
    payload: CodingModelSwitchRequest,
    request: Request,
) -> dict[str, Any]:
    """Switch the model used by a coding session."""
    runtime = _require_runtime(request, session_id)
    allowed = _catalog_model_ids(request)
    if payload.model_id not in allowed:
        raise HTTPException(status_code=422, detail="unknown coding model")
    if runtime.active_run_id is not None or runtime._context_operation_lock.locked():
        raise HTTPException(status_code=409, detail="context operation is busy")
    model_factory = request.app.state.coding_model_factory

    def factory() -> Any:
        return _build_model(model_factory, payload.model_id)

    try:
        runtime.switch_model(payload.model_id, factory)
    except ContextBusyError as exc:
        raise HTTPException(status_code=409, detail="context operation is busy") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="model switch failed") from exc
    return {"ok": True, "model_id": payload.model_id}


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
    from mcp_servers.registry import build_mcp_config

    config = build_mcp_config(
        amap_api_key="",
        qweather_api_key="",
    )
    servers = [
        CodingMcpServer(name=name, transport=spec.get("transport", "stdio"), status="configured")
        for name, spec in config.items()
    ]
    return CodingMcpServersResponse(servers=servers)


def _require_runtime(request: Request, session_id: str) -> CodingRuntime:
    sessions: dict[str, CodingRuntime] = request.app.state.coding_sessions
    runtime = sessions.get(session_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail=f"Unknown coding session: {session_id}")
    return runtime


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


def _build_model(factory: Any, model_id: str) -> Any:
    """Call injected factories with a model id when their signature accepts it."""
    try:
        signature(factory).bind(model_id)
    except (TypeError, ValueError):
        return factory()
    return factory(model_id)


def _catalog_model_ids(request: Request) -> set[str]:
    return {str(item["id"]) for item in request.app.state.coding_model_catalog}


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
