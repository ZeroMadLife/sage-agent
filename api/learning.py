"""Learning-task draft control plane."""

from __future__ import annotations

import asyncio
import re
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sage_harness import SubagentToolConfig

from api.cloud_dependencies import SESSION_COOKIE, require_cloud_authentication_in_production
from api.coding import CodingRuntimeRehydrateError
from api.schemas import (
    LearningActivationResponse,
    LearningAdvanceRequest,
    LearningArtifactResponse,
    LearningErrorResponse,
    LearningKickoffDispatchRequest,
    LearningKickoffDispatchResponse,
    LearningResumeResponse,
    LearningSourcePolicyRequest,
    LearningTaskActivationRequest,
    LearningTaskDraftRequest,
    LearningTaskPatchRequest,
    LearningTaskResponse,
)
from core.cloud.auth.repository import CloudRepository
from core.coding.memory import workspace_id_from_path
from core.harness.evidence_bundle import CodingEvidenceBundlePort
from core.harness.knowledge_adapter import CodingKnowledgePort
from core.harness.learning_scope import LearningReadonlyScopeResolver, LearningScopeConflict
from core.harness.sandbox_factory import create_coding_sandbox
from core.harness.subagent_adapter import CodingSubagentExecutor, build_coding_subagent_config
from core.learning import (
    UNSET,
    LearningActivationError,
    LearningActivationRecord,
    LearningActivationService,
    LearningArtifactStore,
    LearningExecutionContext,
    LearningExecutionService,
    LearningFailureCode,
    LearningKickoffDispatchRecord,
    LearningKickoffError,
    LearningKickoffService,
    LearningMapService,
    LearningResearchService,
    LearningSourcePolicy,
    LearningTask,
    LearningTaskConflictError,
    LearningTaskCreate,
    LearningTaskNotFoundError,
    LearningTaskPatch,
    LearningTaskService,
    UnsetValue,
)
from core.learning.artifact_store import (
    LearningArtifactNotFoundError,
    LearningArtifactStoreError,
    LearningResumeNotFoundError,
)

router = APIRouter(
    prefix="/api/v1/learning",
    dependencies=[Depends(require_cloud_authentication_in_production)],
)

_LEARNING_TASK_ID = re.compile(r"ltask_[0-9a-f]{32}")
_LEARNING_ARTIFACT_ID = re.compile(r"lart_[0-9a-f]{24}")


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
        raise _learning_error(
            422, LearningFailureCode.REQUEST_INVALID, "invalid learning request"
        ) from exc
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
    _validate_learning_task_id(task_id)
    try:
        task = await asyncio.to_thread(
            _service(request).get,
            owner_id=await _owner_id(request),
            workspace_id=_workspace_id(request),
            task_id=task_id,
        )
    except LearningTaskNotFoundError as exc:
        raise _learning_error(
            404, LearningFailureCode.TASK_NOT_FOUND, "learning task not found"
        ) from exc
    except ValueError as exc:
        raise _learning_error(
            422, LearningFailureCode.REQUEST_INVALID, "invalid learning request"
        ) from exc
    return _response(task)


@router.patch("/tasks/{task_id}", response_model=LearningTaskResponse)
async def update_learning_draft(
    task_id: str,
    payload: LearningTaskPatchRequest,
    request: Request,
    response: Response,
) -> LearningTaskResponse:
    response.headers["Cache-Control"] = "no-store"
    _validate_learning_task_id(task_id)
    fields = payload.model_fields_set
    topic_patch: str | UnsetValue = UNSET
    if "topic" in fields:
        if payload.topic is None:
            raise _learning_error(
                422, LearningFailureCode.REQUEST_INVALID, "topic cannot be cleared"
            )
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
        raise _learning_error(
            404, LearningFailureCode.TASK_NOT_FOUND, "learning task not found"
        ) from exc
    except LearningTaskConflictError as exc:
        raise _learning_error(
            409,
            LearningFailureCode.TASK_REVISION_CONFLICT,
            "",
            current_revision=exc.current_revision,
        ) from exc
    except (TypeError, ValueError) as exc:
        raise _learning_error(
            422, LearningFailureCode.REQUEST_INVALID, "invalid learning request"
        ) from exc
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
    _validate_learning_task_id(task_id)
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
        raise _learning_error(
            404, LearningFailureCode.TASK_NOT_FOUND, "learning task not found"
        ) from exc
    except LearningTaskConflictError as exc:
        raise _learning_error(
            409,
            LearningFailureCode.TASK_REVISION_CONFLICT,
            "",
            current_revision=exc.current_revision,
        ) from exc
    except LearningActivationError as exc:
        status_code = 503 if exc.code == "learning_activation_failed" else 409
        raise _learning_error(
            status_code,
            _failure_code(exc.code, LearningFailureCode.ACTIVATION_CONFLICT),
            "learning activation failed",
        ) from exc
    except ValueError as exc:
        raise _learning_error(
            422, LearningFailureCode.REQUEST_INVALID, "invalid learning request"
        ) from exc
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
    _validate_learning_task_id(task_id)
    try:
        receipt = await asyncio.to_thread(
            _activation_service(request).get,
            owner_id=await _owner_id(request),
            workspace_id=_workspace_id(request),
            task_id=task_id,
        )
    except LearningActivationError as exc:
        raise _learning_error(
            404 if exc.code == "learning_activation_not_found" else 409,
            _failure_code(exc.code, LearningFailureCode.ACTIVATION_CONFLICT),
            "learning activation unavailable",
        ) from exc
    except ValueError as exc:
        raise _learning_error(
            422, LearningFailureCode.REQUEST_INVALID, "invalid learning request"
        ) from exc
    return _activation_response(receipt)


@router.post(
    "/tasks/{task_id}/kickoff",
    response_model=LearningKickoffDispatchResponse,
    responses={
        409: {"model": LearningErrorResponse, "description": "Kickoff contract conflict"},
        503: {"model": LearningErrorResponse, "description": "Kickoff dispatch unavailable"},
    },
)
async def dispatch_learning_kickoff(
    task_id: str,
    payload: LearningKickoffDispatchRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
) -> LearningKickoffDispatchResponse:
    response.headers["Cache-Control"] = "no-store"
    _validate_learning_task_id(task_id)
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
        raise _learning_error(
            404, LearningFailureCode.TASK_NOT_FOUND, "learning task not found"
        ) from exc
    except LearningTaskConflictError as exc:
        raise _learning_error(
            409,
            LearningFailureCode.TASK_REVISION_CONFLICT,
            "",
            current_revision=exc.current_revision,
        ) from exc
    except LearningKickoffError as exc:
        raise _learning_error(
            503 if exc.code == "learning_kickoff_dispatch_failed" else 409,
            _failure_code(exc.code, LearningFailureCode.KICKOFF_CONFLICT),
            "learning kickoff failed",
        ) from exc
    except ValueError as exc:
        raise _learning_error(
            422, LearningFailureCode.REQUEST_INVALID, "invalid learning request"
        ) from exc
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
    _validate_learning_task_id(task_id)
    try:
        receipt = await asyncio.to_thread(
            _kickoff_service(request).get,
            owner_id=await _owner_id(request),
            workspace_id=_workspace_id(request),
            task_id=task_id,
        )
    except LearningKickoffError as exc:
        raise _learning_error(
            404 if exc.code == "learning_kickoff_not_found" else 409,
            _failure_code(exc.code, LearningFailureCode.KICKOFF_CONFLICT),
            "learning kickoff unavailable",
        ) from exc
    except ValueError as exc:
        raise _learning_error(
            422, LearningFailureCode.REQUEST_INVALID, "invalid learning request"
        ) from exc
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
    _validate_learning_task_id(task_id)
    try:
        receipt = await asyncio.to_thread(
            _activation_service(request).resume,
            owner_id=await _owner_id(request),
            workspace_id=_workspace_id(request),
            task_id=task_id,
            expected_revision=payload.expected_revision,
        )
    except LearningTaskNotFoundError as exc:
        raise _learning_error(
            404, LearningFailureCode.TASK_NOT_FOUND, "learning task not found"
        ) from exc
    except LearningActivationError as exc:
        raise _learning_error(
            409,
            _failure_code(exc.code, LearningFailureCode.ACTIVATION_CONFLICT),
            "learning activation conflict",
        ) from exc
    except ValueError as exc:
        raise _learning_error(
            422, LearningFailureCode.REQUEST_INVALID, "invalid learning request"
        ) from exc
    return _activation_response(receipt)


@router.post(
    "/tasks/{task_id}/advance",
    response_model=LearningResumeResponse,
    responses={
        404: {"model": LearningErrorResponse},
        409: {"model": LearningErrorResponse},
        422: {"model": LearningErrorResponse},
        503: {"model": LearningErrorResponse},
    },
)
async def advance_learning_task(
    task_id: str,
    payload: LearningAdvanceRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Header(min_length=1, max_length=200, alias="Idempotency-Key"),
) -> LearningResumeResponse:
    response.headers["Cache-Control"] = "no-store"
    _validate_learning_task_id(task_id)
    owner_id = await _owner_id(request)
    workspace_id = _workspace_id(request)
    try:
        task = await asyncio.to_thread(
            _service(request).get,
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=task_id,
        )
        execution, context = await _execution_service(
            request, owner_id=owner_id, workspace_id=workspace_id, task=task
        )
        summary = await execution.advance(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task=task,
            context=context,
            expected_checkpoint_revision=payload.expected_checkpoint_revision,
            idempotency_key=idempotency_key,
        )
    except LearningTaskNotFoundError as exc:
        raise _learning_error(
            404, LearningFailureCode.TASK_NOT_FOUND, "learning task not found"
        ) from exc
    except LearningKickoffError as exc:
        raise _learning_error(
            409,
            _failure_code(exc.code, LearningFailureCode.KICKOFF_CONFLICT),
            "learning kickoff conflict",
        ) from exc
    except CodingRuntimeRehydrateError as exc:
        raise _learning_error(
            503,
            LearningFailureCode.RUNTIME_REHYDRATE_FAILED,
            "learning runtime could not be restored",
        ) from exc
    except sqlite3.Error as exc:
        raise _learning_error(
            503,
            LearningFailureCode.ARTIFACT_STORE_UNAVAILABLE,
            "learning state is temporarily unavailable",
        ) from exc
    except (LearningScopeConflict, LearningArtifactStoreError) as exc:
        raise _learning_execution_http_error(exc) from exc
    except ValueError as exc:
        raise _learning_error(
            422, LearningFailureCode.REQUEST_INVALID, "invalid learning request"
        ) from exc
    return LearningResumeResponse.model_validate(asdict(summary))


@router.get(
    "/tasks/{task_id}/resume",
    response_model=LearningResumeResponse,
    responses={
        404: {"model": LearningErrorResponse},
        409: {"model": LearningErrorResponse},
        422: {"model": LearningErrorResponse},
        503: {"model": LearningErrorResponse},
    },
)
async def get_learning_resume(
    task_id: str,
    request: Request,
    response: Response,
) -> LearningResumeResponse:
    response.headers["Cache-Control"] = "no-store"
    _validate_learning_task_id(task_id)
    owner_id = await _owner_id(request)
    workspace_id = _workspace_id(request)
    try:
        task = await asyncio.to_thread(
            _service(request).get,
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=task_id,
        )
        scope = await asyncio.to_thread(
            _scope_resolver(request).resolve,
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=task_id,
        )
        summary = _artifact_store(request).resume(
            owner_id=owner_id,
            workspace_id=workspace_id,
            task=task,
            capability_revision=scope.capability_revision,
        )
    except LearningTaskNotFoundError as exc:
        raise _learning_error(
            404, LearningFailureCode.TASK_NOT_FOUND, "learning task not found"
        ) from exc
    except sqlite3.Error as exc:
        raise _learning_error(
            503,
            LearningFailureCode.ARTIFACT_STORE_UNAVAILABLE,
            "learning state is temporarily unavailable",
        ) from exc
    except (LearningScopeConflict, LearningArtifactStoreError) as exc:
        raise _learning_execution_http_error(exc) from exc
    return LearningResumeResponse.model_validate(asdict(summary))


@router.get(
    "/tasks/{task_id}/artifacts/{artifact_id}",
    response_model=LearningArtifactResponse,
    responses={
        404: {"model": LearningErrorResponse},
        409: {"model": LearningErrorResponse},
        422: {"model": LearningErrorResponse},
        503: {"model": LearningErrorResponse},
    },
)
async def get_learning_artifact(
    task_id: str,
    artifact_id: str,
    request: Request,
    response: Response,
) -> LearningArtifactResponse:
    response.headers["Cache-Control"] = "no-store"
    _validate_learning_task_id(task_id)
    _validate_learning_artifact_id(artifact_id)
    owner_id = await _owner_id(request)
    workspace_id = _workspace_id(request)
    try:
        await asyncio.to_thread(
            _scope_resolver(request).resolve,
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=task_id,
        )
        artifact = _artifact_store(request).read_artifact(
            owner_id=owner_id,
            workspace_id=workspace_id,
            artifact_ref=f"sage://learning/artifacts/{artifact_id}",
        )
        if artifact.task_id != task_id:
            raise LearningArtifactNotFoundError("Learning Artifact not found")
    except (LearningScopeConflict, LearningArtifactStoreError) as exc:
        raise _learning_execution_http_error(exc) from exc
    except sqlite3.Error as exc:
        raise _learning_error(
            503,
            LearningFailureCode.ARTIFACT_STORE_UNAVAILABLE,
            "learning state is temporarily unavailable",
        ) from exc
    return LearningArtifactResponse.model_validate(asdict(artifact))


def _service(request: Request) -> LearningTaskService:
    service = getattr(request.app.state, "learning_task_service", None)
    if not isinstance(service, LearningTaskService):
        raise _learning_error(
            503,
            LearningFailureCode.TASK_SERVICE_UNAVAILABLE,
            "learning task service is unavailable",
        )
    return service


def _activation_service(request: Request) -> LearningActivationService:
    service = getattr(request.app.state, "learning_activation_service", None)
    if not isinstance(service, LearningActivationService):
        raise _learning_error(
            503,
            LearningFailureCode.ACTIVATION_SERVICE_UNAVAILABLE,
            "learning activation service is unavailable",
        )
    return service


def _kickoff_service(request: Request) -> LearningKickoffService:
    service = getattr(request.app.state, "learning_kickoff_service", None)
    if not isinstance(service, LearningKickoffService):
        raise _learning_error(
            503,
            LearningFailureCode.KICKOFF_SERVICE_UNAVAILABLE,
            "learning kickoff service is unavailable",
        )
    return service


def _artifact_store(request: Request) -> LearningArtifactStore:
    store = getattr(request.app.state, "learning_artifact_store", None)
    if not isinstance(store, LearningArtifactStore):
        raise _learning_error(
            503,
            LearningFailureCode.ARTIFACT_STORE_UNAVAILABLE,
            "learning artifact store is unavailable",
        )
    return store


def _scope_resolver(request: Request) -> LearningReadonlyScopeResolver:
    resolver = getattr(request.app.state, "learning_readonly_scope_resolver", None)
    if not isinstance(resolver, LearningReadonlyScopeResolver):
        raise _learning_error(
            503,
            LearningFailureCode.SCOPE_SERVICE_UNAVAILABLE,
            "learning scope is unavailable",
        )
    return resolver


async def _execution_service(
    request: Request,
    *,
    owner_id: str,
    workspace_id: str,
    task: LearningTask,
) -> tuple[LearningExecutionService, LearningExecutionContext]:
    scope = await asyncio.to_thread(
        _scope_resolver(request).resolve,
        owner_id=owner_id,
        workspace_id=workspace_id,
        task_id=task.task_id,
    )
    kickoff = await asyncio.to_thread(
        _kickoff_service(request).get,
        owner_id=owner_id,
        workspace_id=workspace_id,
        task_id=task.task_id,
    )
    from api.coding import _rehydrate_coding_runtime

    runtime = await _rehydrate_coding_runtime(request, scope.session_id)
    knowledge_factory = getattr(request.app.state, "learning_knowledge_port_factory", None)
    knowledge = (
        knowledge_factory(runtime) if callable(knowledge_factory) else CodingKnowledgePort(runtime)
    )
    knowledge_port = knowledge if scope.knowledge_policy != "disabled" else None
    web_search = (
        getattr(request.app.state, "coding_web_search_port", None)
        if scope.web_policy == "allowed_when_insufficient"
        else None
    )
    web_fetch = (
        getattr(request.app.state, "coding_web_fetch_port", None)
        if scope.web_policy == "allowed_when_insufficient"
        else None
    )
    evidence = CodingEvidenceBundlePort(runtime, authorized_parent_run_id=kickoff.turn_run_id)
    config = build_coding_subagent_config(
        knowledge_port,
        web_search,
        web_fetch,
        evidence_bundle_port=evidence,
        base_config=SubagentToolConfig(),
    )
    research_profiles = tuple(profile for profile in config.profiles if profile.name == "research")
    if research_profiles:
        config = replace(
            config,
            allowed_types=frozenset({"research"}),
            profiles=research_profiles,
        )
    sandbox = create_coding_sandbox(
        runtime.workspace,
        thread_id=runtime.session_id,
        app_env=str(getattr(request.app.state, "cloud_app_env", "development")),
        provider=str(getattr(runtime, "sandbox_provider", "local_workspace")),
        allow_host_shell=False,
        allow_writes=False,
        container_image=str(getattr(runtime, "sandbox_image", "python:3.11-slim")),
    )
    executor = CodingSubagentExecutor(
        runtime,
        knowledge_port=knowledge_port,
        web_search_port=web_search,
        web_fetch_port=web_fetch,
        evidence_bundle_port=evidence,
        sandbox=sandbox,
        allow_shell_network=False,
        web_policy_domains=scope.domains,
        web_policy_freshness="year" if scope.freshness == "current" else "all",
        learning_scope=scope,
        learning_scope_revalidator=lambda: _scope_resolver(request).revalidate(scope),
        authorized_parent_run_id=kickoff.turn_run_id,
    )
    research = LearningResearchService(
        subagent_executor=executor,
        subagent_config=config,
        evidence_bundle_port=evidence,
    )
    return (
        LearningExecutionService(
            store=_artifact_store(request),
            map_service=LearningMapService(
                knowledge_port=knowledge_port,
                learning_scope_revalidator=lambda: scope.assert_current(
                    _scope_resolver(request).revalidate(scope)
                ),
            ),
            research_service=research,
        ),
        LearningExecutionContext(
            thread_id=scope.session_id,
            parent_run_id=kickoff.turn_run_id,
            workspace_path=str(runtime.workspace.root),
            capability_revision=scope.capability_revision,
            catalog_revision=scope.catalog_revision,
            allowed_capabilities=frozenset(scope.allowed_capabilities),
            remaining_token_budget=24_000,
        ),
    )


def _learning_execution_http_error(exc: Exception) -> HTTPException:
    code = _failure_code(
        str(getattr(exc, "code", "")),
        LearningFailureCode.RESUME_VALIDATION_FAILED,
    )
    not_found = isinstance(exc, LearningResumeNotFoundError | LearningArtifactNotFoundError)
    unavailable = code in {
        LearningFailureCode.ARTIFACT_STORE_UNAVAILABLE,
        LearningFailureCode.SCOPE_SERVICE_UNAVAILABLE,
        LearningFailureCode.RUNTIME_REHYDRATE_FAILED,
    }
    return _learning_error(
        404 if not_found else (503 if unavailable else 409),
        code,
        "not found" if not_found else "learning state conflict",
    )


def _failure_code(value: str, fallback: LearningFailureCode) -> LearningFailureCode:
    try:
        return LearningFailureCode(value)
    except ValueError:
        return fallback


def _learning_error(
    status_code: int,
    code: LearningFailureCode,
    message: str,
    *,
    current_revision: int | None = None,
) -> HTTPException:
    detail: dict[str, str | int] = {"code": code.value}
    if message:
        detail["message"] = message
    if current_revision is not None:
        detail["current_revision"] = current_revision
    return HTTPException(status_code=status_code, detail=detail)


def _validate_learning_task_id(task_id: str) -> None:
    if _LEARNING_TASK_ID.fullmatch(task_id) is None:
        raise _learning_error(
            422,
            LearningFailureCode.TASK_INVALID_ID,
            "invalid learning task id",
        )


def _validate_learning_artifact_id(artifact_id: str) -> None:
    if _LEARNING_ARTIFACT_ID.fullmatch(artifact_id) is None:
        raise _learning_error(
            422,
            LearningFailureCode.REQUEST_INVALID,
            "invalid learning artifact id",
        )


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
        raise _learning_error(
            503,
            LearningFailureCode.WORKSPACE_UNAVAILABLE,
            "learning workspace is unavailable",
        )
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
