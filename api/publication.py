"""Private review API for exporting approved public package stage artifacts."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from api.cloud_dependencies import (
    SESSION_COOKIE,
    require_cloud_authentication_in_production,
)
from api.schemas import (
    PublicationCandidateCreateRequest,
    PublicationCandidateDetailResponse,
    PublicationCandidateEventResponse,
    PublicationCandidateResponse,
    PublicationCandidatesResponse,
    PublicationCandidateTransitionRequest,
    PublicationStageArtifactResponse,
)
from core.cloud.auth.repository import CloudRepository
from core.publication import (
    PublicationCandidate,
    PublicationCandidateConflictError,
    PublicationCandidateEvent,
    PublicationCandidateNotFoundError,
    PublicationCandidateService,
    PublicationValidationError,
)

router = APIRouter(
    prefix="/api/v1/publication",
    dependencies=[Depends(require_cloud_authentication_in_production)],
)


@router.post(
    "/candidates",
    response_model=PublicationCandidateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_candidate(
    payload: PublicationCandidateCreateRequest,
    request: Request,
    response: Response,
) -> PublicationCandidateResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        candidate = await _service(request).create(
            owner_id=await _owner_id(request),
            package=payload.package,
            reason=payload.reason,
            evidence_refs=payload.evidence_refs,
        )
    except PublicationValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PublicationCandidateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _candidate_response(candidate)


@router.get("/candidates", response_model=PublicationCandidatesResponse)
async def list_candidates(
    request: Request,
    response: Response,
    candidate_status: Annotated[
        Literal["pending", "approved", "rejected"] | None,
        Query(alias="status"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> PublicationCandidatesResponse:
    response.headers["Cache-Control"] = "no-store"
    candidates = await _service(request).repository.list(
        owner_id=await _owner_id(request), status=candidate_status, limit=limit
    )
    return PublicationCandidatesResponse(
        candidates=[_candidate_response(candidate) for candidate in candidates]
    )


@router.get(
    "/candidates/{candidate_id}",
    response_model=PublicationCandidateDetailResponse,
)
async def get_candidate(
    candidate_id: str,
    request: Request,
    response: Response,
) -> PublicationCandidateDetailResponse:
    response.headers["Cache-Control"] = "no-store"
    owner_id = await _owner_id(request)
    repository = _service(request).repository
    try:
        candidate = await repository.get(candidate_id, owner_id=owner_id)
        events = await repository.events(candidate_id, owner_id=owner_id)
    except PublicationCandidateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="publication candidate not found") from exc
    return PublicationCandidateDetailResponse(
        candidate=_candidate_response(candidate),
        package=candidate.package,
        events=[_event_response(event) for event in events],
    )


@router.post(
    "/candidates/{candidate_id}/approve",
    response_model=PublicationCandidateResponse,
)
async def approve_candidate(
    candidate_id: str,
    payload: PublicationCandidateTransitionRequest,
    request: Request,
    response: Response,
) -> PublicationCandidateResponse:
    return await _transition(candidate_id, payload, request, response, "approved")


@router.post(
    "/candidates/{candidate_id}/reject",
    response_model=PublicationCandidateResponse,
)
async def reject_candidate(
    candidate_id: str,
    payload: PublicationCandidateTransitionRequest,
    request: Request,
    response: Response,
) -> PublicationCandidateResponse:
    return await _transition(candidate_id, payload, request, response, "rejected")


@router.get(
    "/candidates/{candidate_id}/stage-artifact",
    response_model=PublicationStageArtifactResponse,
)
async def export_stage_artifact(
    candidate_id: str,
    request: Request,
    response: Response,
) -> PublicationStageArtifactResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        artifact = await _service(request).stage_artifact(
            candidate_id, owner_id=await _owner_id(request)
        )
    except PublicationCandidateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="publication candidate not found") from exc
    except PublicationCandidateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return PublicationStageArtifactResponse.model_validate(artifact)


async def _transition(
    candidate_id: str,
    payload: PublicationCandidateTransitionRequest,
    request: Request,
    response: Response,
    target_status: str,
) -> PublicationCandidateResponse:
    response.headers["Cache-Control"] = "no-store"
    owner_id = await _owner_id(request)
    try:
        candidate = await _service(request).repository.transition(
            candidate_id,
            owner_id=owner_id,
            expected_revision=payload.expected_revision,
            decided_by=owner_id,
            status=target_status,
        )
    except PublicationCandidateNotFoundError as exc:
        raise HTTPException(status_code=404, detail="publication candidate not found") from exc
    except PublicationCandidateConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _candidate_response(candidate)


def _service(request: Request) -> PublicationCandidateService:
    service = getattr(request.app.state, "publication_candidate_service", None)
    if not isinstance(service, PublicationCandidateService):
        raise HTTPException(status_code=503, detail="publication review is unavailable")
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


def _candidate_response(candidate: PublicationCandidate) -> PublicationCandidateResponse:
    documents = candidate.package.get("documents")
    return PublicationCandidateResponse(
        candidate_id=candidate.candidate_id,
        package_id=candidate.package_id,
        package_revision=candidate.package_revision,
        package_digest=candidate.package_digest,
        document_count=len(documents) if isinstance(documents, list) else 0,
        reason=candidate.reason,
        evidence_refs=list(candidate.evidence_refs),
        status=candidate.status,
        revision=candidate.revision,
        decided_by=candidate.decided_by,
        decided_at=candidate.decided_at,
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
    )


def _event_response(event: PublicationCandidateEvent) -> PublicationCandidateEventResponse:
    return PublicationCandidateEventResponse(
        event_id=event.event_id,
        candidate_id=event.candidate_id,
        sequence=event.sequence,
        event_type=event.event_type,
        revision=event.revision,
        detail=event.detail,
        created_at=event.created_at,
    )
