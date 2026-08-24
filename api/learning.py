"""Learning-task draft control plane."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from api.cloud_dependencies import SESSION_COOKIE, require_cloud_authentication_in_production
from api.schemas import (
    LearningActivationResponse,
    LearningKickoffDispatchRequest,
    LearningKickoffDispatchResponse,
    LearningSourcePolicyRequest,
    LearningTaskActivationRequest,
    LearningTaskDraftRequest,
    LearningTaskPatchRequest,
    LearningTaskResponse,
)
from core.cloud.auth.repository import CloudRepository
from core.coding.memory import workspace_id_from_path
from core.learning import (
    UNSET,
    LearningActivationError,
    LearningActivationRecord,
    LearningActivationService,
    LearningKickoffDispatchRecord,
    LearningKickoffError,
    LearningKickoffService,
    LearningSourcePolicy,
    LearningTask,
    LearningTaskConflictError,
    LearningTaskCreate,
    LearningTaskNotFoundError,
    LearningTaskPatch,
    LearningTaskService,
    UnsetValue,
)

router = APIRouter(
    prefix="/api/v1/learning",
    dependencies=[Depends(require_cloud_authentication_in_production)],
)


@router.post(
    "/tasks/draft", response_model=LearningTaskResponse, status_code=status.HTTP_201_CREATED
)
async def create_learning_draft(
    payload: LearningTaskDraftRequest,
    request: Request,
    response: Response,
) -> LearningTaskResponse:
    response.headers["Cache-Control"] = "no-store"
    service = _service(request)
    try:
        task = await asyncio.to_thread(
            service.create_draft,
            owner_id=await _owner_id(request),
            workspace_id=_workspace_id(request),
            request=LearningTaskCreate(
                topic=payload.topic,
                desired_outcome=payload.desired_outcome,
                starting_level=payload.starting_level,
                time_budget_minutes_per_week=payload.time_budget_minutes_per_week,
                target_date=payload.target_date,
                source_policy=_source_policy(payload.source_policy),
            ),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _response(task)


@router.get("/tasks", response_model=list[LearningTaskResponse])
async def list_learning_tasks(
    request: Request,
    response: Response,
) -> list[LearningTaskResponse]:
    response.headers["Cache-Control"] = "no-store"
    tasks = await asyncio.to_thread(
        _service(request).list,
        owner_id=await _owner_id(request),
        workspace_id=_workspace_id(request),
    )
    return [_response(task) for task in tasks]


@router.get("/tasks/{task_id}", response_model=LearningTaskResponse)
async def get_learning_task(
    task_id: str,
    request: Request,
    response: Response,
) -> LearningTaskResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        task = await asyncio.to_thread(
            _service(request).get,
            owner_id=await _owner_id(request),
            workspace_id=_workspace_id(request),
            task_id=task_id,
        )
    except LearningTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="learning task not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _response(task)


@router.patch("/tasks/{task_id}", response_model=LearningTaskResponse)
async def update_learning_draft(
    task_id: str,
    payload: LearningTaskPatchRequest,
    request: Request,
    response: Response,
) -> LearningTaskResponse:
    response.headers["Cache-Control"] = "no-store"
    fields = payload.model_fields_set
    topic_patch: str | UnsetValue = UNSET
    if "topic" in fields:
        if payload.topic is None:
            raise HTTPException(status_code=422, detail="topic cannot be cleared")
        topic_patch = payload.topic
    try:
        task = await asyncio.to_thread(
            _service(request).update_draft,
            owner_id=await _owner_id(request),
            workspace_id=_workspace_id(request),
            task_id=task_id,
            expected_revision=payload.expected_revision,
            patch=LearningTaskPatch(
                topic=topic_patch,
                desired_outcome=payload.desired_outcome if "desired_outcome" in fields else UNSET,
                starting_level=payload.starting_level if "starting_level" in fields else UNSET,
                time_budget_minutes_per_week=(
                    payload.time_budget_minutes_per_week
                    if "time_budget_minutes_per_week" in fields
                    else UNSET
                ),
                target_date=payload.target_date if "target_date" in fields else UNSET,
                source_policy=(
                    _source_policy(payload.source_policy) if "source_policy" in fields else UNSET
                ),
            ),
        )
    except LearningTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="learning task not found") from exc
    except LearningTaskConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "learning_task_revision_conflict",
                "current_revision": exc.current_revision,
            },
        ) from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _response(task)


@router.post(
    "/tasks/{task_id}/activate",
    response_model=LearningActivationResponse,
)
async def activate_learning_task(
    task_id: str,
    payload: LearningTaskActivationRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
) -> LearningActivationResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        receipt = await asyncio.to_thread(
            _activation_service(request).activate,
            owner_id=await _owner_id(request),
            workspace_id=_workspace_id(request),
            task_id=task_id,
            expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key,
        )
    except LearningTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="learning task not found") from exc
    except LearningTaskConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "learning_task_revision_conflict",
                "current_revision": exc.current_revision,
            },
        ) from exc
    except LearningActivationError as exc:
        status_code = 503 if exc.code == "learning_activation_failed" else 409
        raise HTTPException(
            status_code=status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _activation_response(receipt)


@router.get(
    "/tasks/{task_id}/activation",
    response_model=LearningActivationResponse,
)
async def get_learning_activation(
    task_id: str,
    request: Request,
    response: Response,
) -> LearningActivationResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        receipt = await asyncio.to_thread(
            _activation_service(request).get,
            owner_id=await _owner_id(request),
            workspace_id=_workspace_id(request),
            task_id=task_id,
        )
    except LearningActivationError as exc:
        raise HTTPException(
            status_code=404 if exc.code == "learning_activation_not_found" else 409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _activation_response(receipt)


@router.post(
    "/tasks/{task_id}/kickoff",
    response_model=LearningKickoffDispatchResponse,
)
async def dispatch_learning_kickoff(
    task_id: str,
    payload: LearningKickoffDispatchRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
) -> LearningKickoffDispatchResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        receipt = await asyncio.to_thread(
            _kickoff_service(request).dispatch,
            owner_id=await _owner_id(request),
            workspace_id=_workspace_id(request),
            task_id=task_id,
            expected_revision=payload.expected_revision,
            idempotency_key=idempotency_key,
        )
    except LearningTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="learning task not found") from exc
    except LearningTaskConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "learning_task_revision_conflict",
                "current_revision": exc.current_revision,
            },
        ) from exc
    except LearningKickoffError as exc:
        raise HTTPException(
            status_code=503 if exc.code == "learning_kickoff_dispatch_failed" else 409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _kickoff_response(receipt)


@router.get(
    "/tasks/{task_id}/kickoff",
    response_model=LearningKickoffDispatchResponse,
)
async def get_learning_kickoff(
    task_id: str,
    request: Request,
    response: Response,
) -> LearningKickoffDispatchResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        receipt = await asyncio.to_thread(
            _kickoff_service(request).get,
            owner_id=await _owner_id(request),
            workspace_id=_workspace_id(request),
            task_id=task_id,
        )
    except LearningKickoffError as exc:
        raise HTTPException(
            status_code=404 if exc.code == "learning_kickoff_not_found" else 409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _kickoff_response(receipt)


@router.post(
    "/tasks/{task_id}/resume",
    response_model=LearningActivationResponse,
)
async def resume_learning_task(
    task_id: str,
    payload: LearningTaskActivationRequest,
    request: Request,
    response: Response,
) -> LearningActivationResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        receipt = await asyncio.to_thread(
            _activation_service(request).resume,
            owner_id=await _owner_id(request),
            workspace_id=_workspace_id(request),
            task_id=task_id,
            expected_revision=payload.expected_revision,
        )
    except LearningTaskNotFoundError as exc:
        raise HTTPException(status_code=404, detail="learning task not found") from exc
    except LearningActivationError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _activation_response(receipt)


def _service(request: Request) -> LearningTaskService:
    service = getattr(request.app.state, "learning_task_service", None)
    if not isinstance(service, LearningTaskService):
        raise HTTPException(status_code=503, detail="learning task service is unavailable")
    return service


def _activation_service(request: Request) -> LearningActivationService:
    service = getattr(request.app.state, "learning_activation_service", None)
    if not isinstance(service, LearningActivationService):
        raise HTTPException(status_code=503, detail="learning activation service is unavailable")
    return service


def _kickoff_service(request: Request) -> LearningKickoffService:
    service = getattr(request.app.state, "learning_kickoff_service", None)
    if not isinstance(service, LearningKickoffService):
        raise HTTPException(status_code=503, detail="learning kickoff service is unavailable")
    return service


async def _owner_id(request: Request) -> str:
    repository = getattr(request.app.state, "cloud_repository", None)
    if isinstance(repository, CloudRepository):
        user = await repository.authenticated_user(request.cookies.get(SESSION_COOKIE, ""))
        if user is not None:
            return user.user_id
    if str(getattr(request.app.state, "cloud_app_env", "development")).lower() != "production":
        return "local"
    raise HTTPException(status_code=401, detail="cloud authentication is required")


def _workspace_id(request: Request) -> str:
    root = getattr(request.app.state, "coding_workspace_root", None)
    if not isinstance(root, Path):
        raise HTTPException(status_code=503, detail="learning workspace is unavailable")
    return workspace_id_from_path(root)


def _source_policy(payload: LearningSourcePolicyRequest | None) -> LearningSourcePolicy | None:
    if payload is None:
        return None
    return LearningSourcePolicy(
        knowledge=payload.knowledge,
        web=payload.web,
        domains=tuple(payload.domains),
        freshness=payload.freshness,
    )


def _response(task: LearningTask) -> LearningTaskResponse:
    return LearningTaskResponse.model_validate(asdict(task))


def _activation_response(record: LearningActivationRecord) -> LearningActivationResponse:
    payload = asdict(record)
    # Expand-phase response aliases keep existing clients working. They are not
    # persisted and must never be used as an internal plan authority.
    payload["plan_id"] = record.turn_context_plan_id
    payload["plan_hash"] = record.turn_context_plan_hash
    return LearningActivationResponse.model_validate(payload)


def _kickoff_response(
    record: LearningKickoffDispatchRecord,
) -> LearningKickoffDispatchResponse:
    payload = asdict(record)
    payload.pop("owner_id")
    payload.pop("idempotency_key")
    return LearningKickoffDispatchResponse.model_validate(payload)


__all__ = ["router"]
