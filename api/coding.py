"""Coding-agent REST and WebSocket routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, WebSocket

from api.schemas import (
    CodingApprovalRespondRequest,
    CodingApprovalResponse,
    CodingFileContentResponse,
    CodingFileEntry,
    CodingFilesResponse,
    CodingGitStatusResponse,
    CodingMcpServer,
    CodingMcpServersResponse,
    CodingModel,
    CodingModelsResponse,
    CodingModelSwitchRequest,
    CodingRunDetailResponse,
    CodingRunsResponse,
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
from core.coding.persistence import CodingSessionStore
from core.coding.runtime import CodingRuntime
from core.llm import create_llm

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
    session_id = str(uuid4())
    runtime = CodingRuntime(
        session_id=session_id,
        workspace_root=workspace_root,
        model=model_factory(),
        storage_root=storage_root,
        model_factory=model_factory,
        approval_policy=payload.approval_policy,
        save_on_init=False,
        permission_mode="default",
    )
    sessions: dict[str, CodingRuntime] = request.app.state.coding_sessions
    sessions[session_id] = runtime
    return CodingSessionResponse(
        session_id=session_id, workspace_root=str(workspace_root.resolve())
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
    try:
        runtime = CodingRuntime.resume(
            session_id=session_id,
            model=model_factory(),
            storage_root=storage_root,
            model_factory=model_factory,
            approval_policy="ask",
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown coding session: {session_id}"
        ) from exc
    default_workspace = Path(request.app.state.coding_workspace_root).resolve()
    try:
        runtime.workspace.root.resolve().relative_to(default_workspace)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="persisted workspace_root must be inside the configured coding workspace",
        ) from exc
    sessions: dict[str, CodingRuntime] = request.app.state.coding_sessions
    sessions[session_id] = runtime
    return CodingSessionResponse(
        session_id=session_id,
        workspace_root=str(runtime.workspace.root.resolve()),
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
async def stop_coding_run(session_id: str, request: Request) -> dict[str, bool]:
    """Request cancellation for the active coding run."""
    runtime = _require_runtime(request, session_id)
    runtime.request_stop()
    return {"ok": True}


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


@router.get("/api/v1/coding/models", response_model=CodingModelsResponse)
async def list_coding_models(request: Request) -> CodingModelsResponse:
    """Return available models (simplified: deepseek v4 flash/pro only)."""
    models = [
        CodingModel(id="deepseek:deepseek-v4-flash", label="DeepSeek V4 Flash", provider="deepseek"),
        CodingModel(id="deepseek:deepseek-v4-pro", label="DeepSeek V4 Pro", provider="deepseek"),
    ]
    return CodingModelsResponse(models=models, current="deepseek:deepseek-v4-flash")


@router.patch("/api/v1/coding/{session_id}/model")
async def switch_coding_model(
    session_id: str,
    payload: CodingModelSwitchRequest,
    request: Request,
) -> dict[str, Any]:
    """Switch the model used by a coding session."""
    runtime = _require_runtime(request, session_id)

    def factory() -> Any:
        return create_llm(payload.model_id)

    try:
        runtime.switch_model(payload.model_id, factory)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "model_id": payload.model_id}


@router.patch("/api/v1/coding/{session_id}/permission-mode")
async def switch_permission_mode(
    session_id: str, payload: PermissionModeSwitchRequest, request: Request
) -> dict:
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
