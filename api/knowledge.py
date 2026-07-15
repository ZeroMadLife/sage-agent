"""Local V7.2 Knowledge Workspace review and rollback routes."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from api.schemas import (
    KnowledgeBatchIngestRequest,
    KnowledgeIngestRequest,
    KnowledgeJobEventResponse,
    KnowledgeJobEventsResponse,
    KnowledgeJobItemResponse,
    KnowledgeJobResponse,
    KnowledgeJobsResponse,
    KnowledgeMigrationApplyRequest,
    KnowledgeMigrationPlanItemResponse,
    KnowledgeMigrationPlanResponse,
    KnowledgeMigrationResultItemResponse,
    KnowledgeMigrationResultResponse,
    KnowledgePageResponse,
    KnowledgePagesResponse,
    KnowledgeParseArtifactResponse,
    KnowledgeParseBlockResponse,
    KnowledgePolicyDecisionResponse,
    KnowledgeProposalDetailResponse,
    KnowledgeProposalEvent,
    KnowledgeProposalResponse,
    KnowledgeProposalsResponse,
    KnowledgeRollbackRequest,
    KnowledgeSourceRootSummary,
    KnowledgeSourceUnderstandingResponse,
    KnowledgeSynthesisSourceResponse,
    KnowledgeTransitionRequest,
    KnowledgeUnderstandingCitationResponse,
    KnowledgeUnderstandingSectionResponse,
    KnowledgeUndoAutoApplyRequest,
    KnowledgeWorkspaceSummary,
    KnowledgeWorkspaceSynthesisResponse,
)
from core.knowledge import (
    KnowledgeConflictError,
    KnowledgeMigrationPlan,
    KnowledgeMigrationResult,
    KnowledgePage,
    KnowledgePolicyDecision,
    KnowledgeProjectionError,
    KnowledgeProposal,
    KnowledgeStore,
    SourceUnderstanding,
    WorkspaceSynthesis,
)
from core.knowledge.jobs import (
    TERMINAL_JOB_STATUSES,
    KnowledgeJob,
    KnowledgeJobConflictError,
    KnowledgeJobEvent,
    KnowledgeJobItem,
    KnowledgeJobNotFoundError,
    KnowledgeJobService,
    KnowledgeScanError,
)
from core.knowledge.parsing import ParseArtifact

router = APIRouter()
_MAX_DIFF_CHARS = 200_000


@router.get("/api/v1/knowledge", response_model=KnowledgeWorkspaceSummary)
async def get_knowledge_summary(request: Request, response: Response) -> KnowledgeWorkspaceSummary:
    store = _require_store(request)
    summary = store.summary()
    response.headers["Cache-Control"] = "no-store"
    return KnowledgeWorkspaceSummary(
        status="ready",
        workspace_name=summary.workspace_name,
        source_count=summary.source_count,
        wiki_page_count=summary.wiki_page_count,
        pending_proposal_count=summary.pending_proposal_count,
        last_synced_at=summary.last_synced_at,
        source_roots=[
            KnowledgeSourceRootSummary(
                root_id=item.root_id,
                kind=item.kind,  # type: ignore[arg-type]
                label=item.label,
            )
            for item in summary.source_roots
        ],
    )


@router.post(
    "/api/v1/knowledge/ingest",
    response_model=KnowledgeProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_knowledge_source(
    payload: KnowledgeIngestRequest, request: Request
) -> KnowledgeProposalResponse:
    store = _require_store(request)
    try:
        proposal = store.ingest(payload.source_root_id, payload.relative_path)
        proposal = store.evaluate_and_apply_policy(proposal.proposal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge source root not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="knowledge source not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KnowledgeConflictError as exc:
        raise HTTPException(status_code=409, detail="knowledge source conflict") from exc
    return _proposal_response(store, proposal)


@router.post(
    "/api/v1/knowledge/jobs",
    response_model=KnowledgeJobResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_job(
    payload: KnowledgeBatchIngestRequest, request: Request
) -> KnowledgeJobResponse:
    service = _require_job_service(request)
    try:
        job = await service.create_batch(payload.source_root_id, payload.relative_directory)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge source root not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="knowledge source directory not found") from exc
    except KnowledgeScanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _job_response(job)


@router.get("/api/v1/knowledge/jobs", response_model=KnowledgeJobsResponse)
async def list_knowledge_jobs(
    request: Request, limit: int = Query(default=30, ge=1, le=100)
) -> KnowledgeJobsResponse:
    service = _require_job_service(request)
    jobs = await service.repository.list_jobs(limit=limit)
    responses: list[KnowledgeJobResponse] = []
    for job in jobs:
        responses.append(
            _job_response(
                job,
                items=await service.repository.list_items(
                    job.job_id,
                    statuses={"dead_letter"},
                    limit=100,
                ),
            )
        )
    return KnowledgeJobsResponse(jobs=responses)


@router.get(
    "/api/v1/knowledge/migrations/pending",
    response_model=KnowledgeMigrationPlanResponse,
)
async def get_pending_knowledge_migration(request: Request) -> KnowledgeMigrationPlanResponse:
    store = _require_store(request)
    return _migration_plan_response(store.plan_pending_migration())


@router.post(
    "/api/v1/knowledge/migrations/pending/apply",
    response_model=KnowledgeMigrationResultResponse,
)
async def apply_pending_knowledge_migration(
    payload: KnowledgeMigrationApplyRequest,
    request: Request,
) -> KnowledgeMigrationResultResponse:
    store = _require_store(request)
    try:
        result = store.execute_pending_migration(payload.expected_plan_id)
    except KnowledgeConflictError as exc:
        raise HTTPException(status_code=409, detail="knowledge migration plan changed") from exc
    return _migration_result_response(result)


@router.get("/api/v1/knowledge/jobs/{job_id}", response_model=KnowledgeJobResponse)
async def get_knowledge_job(
    job_id: str,
    request: Request,
    include_items: bool = Query(default=True),
) -> KnowledgeJobResponse:
    service = _require_job_service(request)
    try:
        job = await service.repository.get_job(job_id)
        items = await service.repository.list_items(job_id) if include_items else []
    except KnowledgeJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="knowledge job not found") from exc
    return _job_response(job, items=items)


@router.get(
    "/api/v1/knowledge/jobs/{job_id}/events",
    response_model=KnowledgeJobEventsResponse,
)
async def get_knowledge_job_events(
    job_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
) -> KnowledgeJobEventsResponse:
    service = _require_job_service(request)
    try:
        events = await service.repository.list_events(job_id, after=after, limit=limit + 1)
    except KnowledgeJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="knowledge job not found") from exc
    has_more = len(events) > limit
    visible = events[:limit]
    return KnowledgeJobEventsResponse(
        items=[_job_event_response(event) for event in visible],
        next_cursor=visible[-1].sequence if visible else after,
        has_more=has_more,
    )


@router.post("/api/v1/knowledge/jobs/{job_id}/cancel", response_model=KnowledgeJobResponse)
async def cancel_knowledge_job(job_id: str, request: Request) -> KnowledgeJobResponse:
    service = _require_job_service(request)
    try:
        return _job_response(await service.cancel_job(job_id))
    except KnowledgeJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="knowledge job not found") from exc
    except KnowledgeJobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/api/v1/knowledge/jobs/{job_id}/items/{item_id}/retry",
    response_model=KnowledgeJobItemResponse,
)
async def retry_knowledge_job_item(
    job_id: str, item_id: str, request: Request
) -> KnowledgeJobItemResponse:
    service = _require_job_service(request)
    try:
        return _job_item_response(await service.retry_item(job_id, item_id))
    except KnowledgeJobNotFoundError as exc:
        raise HTTPException(status_code=404, detail="knowledge job item not found") from exc
    except KnowledgeJobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.websocket("/api/v1/knowledge/jobs/{job_id}/stream")
async def stream_knowledge_job(
    websocket: WebSocket, job_id: str, after: int = Query(default=0, ge=0)
) -> None:
    service = _job_service_for_websocket(websocket)
    if service is None:
        await websocket.close(code=1013, reason="knowledge jobs unavailable")
        return
    try:
        job = await service.repository.get_job(job_id)
    except KnowledgeJobNotFoundError:
        await websocket.close(code=1008, reason="knowledge job not found")
        return
    await websocket.accept()
    cursor = after
    try:
        while True:
            events = await service.repository.list_events(job_id, after=cursor, limit=200)
            for event in events:
                await websocket.send_json(_job_event_response(event).model_dump())
                cursor = event.sequence
            job = await service.repository.get_job(job_id)
            if job.status in TERMINAL_JOB_STATUSES and cursor >= job.latest_sequence:
                await websocket.close(code=1000)
                return
            await asyncio.sleep(0.2)
    except WebSocketDisconnect:
        return


@router.get("/api/v1/knowledge/proposals", response_model=KnowledgeProposalsResponse)
async def list_knowledge_proposals(
    request: Request,
    proposal_status: Literal["pending", "approved", "rejected"] | None = Query(
        default=None, alias="status"
    ),
) -> KnowledgeProposalsResponse:
    store = _require_store(request)
    return KnowledgeProposalsResponse(
        proposals=[
            _proposal_response(store, proposal)
            for proposal in store.list_proposals(proposal_status)
        ]
    )


@router.post(
    "/api/v1/knowledge/synthesis",
    response_model=KnowledgeProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_synthesis(request: Request) -> KnowledgeProposalResponse:
    store = _require_store(request)
    try:
        proposal = store.propose_workspace_synthesis()
        proposal = store.evaluate_and_apply_policy(proposal.proposal_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail="workspace synthesis requires approved knowledge sources",
        ) from exc
    return _proposal_response(store, proposal)


@router.get(
    "/api/v1/knowledge/proposals/{proposal_id}",
    response_model=KnowledgeProposalDetailResponse,
)
async def get_knowledge_proposal(
    proposal_id: str, request: Request
) -> KnowledgeProposalDetailResponse:
    store = _require_store(request)
    try:
        proposal = store.get_proposal(proposal_id)
        events = store.list_events(proposal_id)
        parse_artifact = store.get_parse_artifact(proposal_id)
        source_understanding = store.get_source_understanding(proposal_id)
        workspace_synthesis = store.get_workspace_synthesis(proposal_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="knowledge proposal not found") from exc
    return KnowledgeProposalDetailResponse(
        proposal=_proposal_response(store, proposal),
        events=[
            KnowledgeProposalEvent(
                event_id=event.event_id,
                event_type=event.event_type,
                revision=event.revision,
                detail=event.detail,
                created_at=event.created_at,
            )
            for event in events
        ],
        parse_artifact=_parse_artifact_response(parse_artifact),
        source_understanding=_source_understanding_response(source_understanding),
        workspace_synthesis=_workspace_synthesis_response(workspace_synthesis),
    )


@router.post(
    "/api/v1/knowledge/proposals/{proposal_id}/approve",
    response_model=KnowledgeProposalResponse,
)
async def approve_knowledge_proposal(
    proposal_id: str, payload: KnowledgeTransitionRequest, request: Request
) -> KnowledgeProposalResponse:
    store = _require_store(request)
    try:
        proposal = store.approve(proposal_id, payload.expected_revision)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge proposal not found") from exc
    except KnowledgeConflictError as exc:
        raise HTTPException(status_code=409, detail="knowledge revision conflict") from exc
    except KnowledgeProjectionError as exc:
        raise HTTPException(status_code=500, detail="knowledge projection failed") from exc
    return _proposal_response(store, proposal)


@router.post(
    "/api/v1/knowledge/proposals/{proposal_id}/reject",
    response_model=KnowledgeProposalResponse,
)
async def reject_knowledge_proposal(
    proposal_id: str, payload: KnowledgeTransitionRequest, request: Request
) -> KnowledgeProposalResponse:
    store = _require_store(request)
    try:
        proposal = store.reject(proposal_id, payload.expected_revision)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge proposal not found") from exc
    except KnowledgeConflictError as exc:
        raise HTTPException(status_code=409, detail="knowledge revision conflict") from exc
    return _proposal_response(store, proposal)


@router.post(
    "/api/v1/knowledge/proposals/{proposal_id}/undo-auto-apply",
    response_model=KnowledgeProposalResponse,
)
async def undo_knowledge_auto_apply(
    proposal_id: str, payload: KnowledgeUndoAutoApplyRequest, request: Request
) -> KnowledgeProposalResponse:
    store = _require_store(request)
    try:
        proposal = store.undo_auto_apply(
            proposal_id,
            expected_page_revision=payload.expected_page_revision,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge proposal not found") from exc
    except KnowledgeConflictError as exc:
        raise HTTPException(status_code=409, detail="knowledge revision conflict") from exc
    except KnowledgeProjectionError as exc:
        raise HTTPException(status_code=500, detail="knowledge projection failed") from exc
    return _proposal_response(store, proposal)


@router.get("/api/v1/knowledge/pages", response_model=KnowledgePagesResponse)
async def list_knowledge_pages(request: Request) -> KnowledgePagesResponse:
    store = _require_store(request)
    return KnowledgePagesResponse(pages=[_page_response(page) for page in store.list_pages()])


@router.post(
    "/api/v1/knowledge/pages/{page_id}/rollback",
    response_model=KnowledgeProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def propose_knowledge_rollback(
    page_id: str, payload: KnowledgeRollbackRequest, request: Request
) -> KnowledgeProposalResponse:
    store = _require_store(request)
    try:
        proposal = store.propose_rollback(
            page_id,
            target_revision_id=payload.target_revision_id,
            expected_page_revision=payload.expected_page_revision,
        )
        proposal = store.evaluate_and_apply_policy(proposal.proposal_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge page revision not found") from exc
    except (ValueError, KnowledgeConflictError) as exc:
        raise HTTPException(status_code=409, detail="knowledge revision conflict") from exc
    return _proposal_response(store, proposal)


def _require_store(request: Request) -> KnowledgeStore:
    if str(getattr(request.app.state, "cloud_app_env", "development")) == "production":
        raise HTTPException(
            status_code=503,
            detail="knowledge workspace requires tenant isolation before cloud use",
        )
    store = getattr(request.app.state, "knowledge_store", None)
    if not isinstance(store, KnowledgeStore):
        raise HTTPException(status_code=503, detail="knowledge workspace is not configured")
    return store


def _require_job_service(request: Request) -> KnowledgeJobService:
    _require_store(request)
    service = getattr(request.app.state, "knowledge_job_service", None)
    if not isinstance(service, KnowledgeJobService):
        raise HTTPException(status_code=503, detail="knowledge jobs are not configured")
    return service


def _job_service_for_websocket(websocket: WebSocket) -> KnowledgeJobService | None:
    if str(getattr(websocket.app.state, "cloud_app_env", "development")) == "production":
        return None
    service = getattr(websocket.app.state, "knowledge_job_service", None)
    return service if isinstance(service, KnowledgeJobService) else None


def _job_response(
    job: KnowledgeJob, *, items: list[KnowledgeJobItem] | None = None
) -> KnowledgeJobResponse:
    return KnowledgeJobResponse(
        job_id=job.job_id,
        workspace_id=job.workspace_id,
        source_root_id=job.source_root_id,
        source_kind=job.source_kind,
        source_label=job.source_label,
        relative_directory=job.relative_directory,
        pipeline_version=job.pipeline_version,
        status=job.status,
        cancel_requested=job.cancel_requested,
        total_items=job.total_items,
        processed_items=job.processed_items,
        succeeded_items=job.succeeded_items,
        skipped_items=job.skipped_items,
        failed_items=job.failed_items,
        cancelled_items=job.cancelled_items,
        latest_sequence=job.latest_sequence,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        updated_at=job.updated_at.isoformat(),
        items=[_job_item_response(item) for item in items or []],
    )


def _job_item_response(item: KnowledgeJobItem) -> KnowledgeJobItemResponse:
    return KnowledgeJobItemResponse(
        item_id=item.item_id,
        job_id=item.job_id,
        relative_path=item.relative_path,
        source_revision=item.source_revision,
        status=item.status,
        attempts=item.attempts,
        max_attempts=item.max_attempts,
        proposal_id=item.proposal_id,
        error=item.error,
        next_attempt_at=(item.next_attempt_at.isoformat() if item.next_attempt_at else None),
        updated_at=item.updated_at.isoformat(),
    )


def _job_event_response(event: KnowledgeJobEvent) -> KnowledgeJobEventResponse:
    return KnowledgeJobEventResponse(
        event_id=event.event_id,
        job_id=event.job_id,
        item_id=event.item_id,
        sequence=event.sequence,
        kind=event.kind,
        status=event.status,
        detail=event.detail,
        created_at=event.created_at.isoformat(),
    )


def _proposal_response(
    store: KnowledgeStore, proposal: KnowledgeProposal
) -> KnowledgeProposalResponse:
    diff = store.proposal_diff(proposal)
    truncated = len(diff) > _MAX_DIFF_CHARS
    if truncated:
        diff = diff[:_MAX_DIFF_CHARS] + "\n... diff truncated ...\n"
    return KnowledgeProposalResponse(
        proposal_id=proposal.proposal_id,
        source_root_id=proposal.source_root_id,
        source_kind=proposal.source_kind,
        source_relative_path=proposal.source_relative_path,
        source_revision=proposal.source_revision,
        raw_path=proposal.raw_path,
        page_id=proposal.page_id,
        target_path=proposal.target_path,
        title=proposal.title,
        base_page_revision=proposal.base_page_revision,
        change_kind=proposal.change_kind,  # type: ignore[arg-type]
        status=proposal.status,  # type: ignore[arg-type]
        projection_status=proposal.projection_status,  # type: ignore[arg-type]
        revision=proposal.revision,
        parse_artifact_id=proposal.parse_artifact_id,
        error=proposal.error,
        policy_decision=_policy_decision_response(store.get_policy_decision(proposal.proposal_id)),
        diff=diff,
        diff_truncated=truncated,
        created_at=proposal.created_at,
        updated_at=proposal.updated_at,
    )


def _migration_plan_response(plan: KnowledgeMigrationPlan) -> KnowledgeMigrationPlanResponse:
    return KnowledgeMigrationPlanResponse(
        plan_id=plan.plan_id,
        total=plan.total,
        auto_apply_count=plan.count("auto_apply"),
        retire_count=plan.count("retire"),
        review_count=plan.count("review"),
        block_count=plan.count("block"),
        items=[
            KnowledgeMigrationPlanItemResponse(
                proposal_id=item.proposal_id,
                source_root_id=item.source_root_id,
                source_relative_path=item.source_relative_path,
                disposition=item.disposition,  # type: ignore[arg-type]
                reason_codes=list(item.reason_codes),
                parser_id=item.parser_id,
            )
            for item in plan.items
        ],
    )


def _migration_result_response(
    result: KnowledgeMigrationResult,
) -> KnowledgeMigrationResultResponse:
    return KnowledgeMigrationResultResponse(
        plan_id=result.plan_id,
        status=result.status,  # type: ignore[arg-type]
        total=len(result.items),
        auto_applied_count=result.count("auto_applied"),
        retired_count=result.count("retired"),
        review_count=result.count("review"),
        blocked_count=result.count("blocked"),
        error_count=result.count("error"),
        items=[
            KnowledgeMigrationResultItemResponse(
                proposal_id=item.proposal_id,
                status=item.status,  # type: ignore[arg-type]
                replacement_proposal_id=item.replacement_proposal_id,
                reason_code=item.reason_code,
            )
            for item in result.items
        ],
    )


def _policy_decision_response(
    decision: KnowledgePolicyDecision | None,
) -> KnowledgePolicyDecisionResponse | None:
    if decision is None:
        return None
    return KnowledgePolicyDecisionResponse(
        decision_id=decision.decision_id,
        policy_id=decision.policy_id,
        policy_version=decision.policy_version,
        risk_level=decision.risk_level,  # type: ignore[arg-type]
        action=decision.action,  # type: ignore[arg-type]
        reason_codes=list(decision.reason_codes),
        applied_page_revision=decision.applied_page_revision,
        undo_available=(
            decision.action == "auto_apply"
            and decision.applied_page_revision is not None
            and decision.undone_at is None
        ),
        undo_proposal_id=decision.undo_proposal_id,
        undo_page_revision=decision.undo_page_revision,
        undone_at=decision.undone_at,
    )


def _parse_artifact_response(
    artifact: ParseArtifact | None,
) -> KnowledgeParseArtifactResponse | None:
    if artifact is None:
        return None
    document = artifact.document
    return KnowledgeParseArtifactResponse(
        artifact_id=artifact.artifact_id,
        document_id=document.document_id,
        parser_id=document.provenance.parser_id,
        parser_version=document.provenance.parser_version,
        source_revision=document.source_revision,
        media_type=document.provenance.media_type,
        title=document.title,
        language=document.language,
        block_count=len(document.blocks),
        blocks=[
            KnowledgeParseBlockResponse(
                block_id=block.block_id,
                ordinal=block.ordinal,
                kind=block.kind,
                heading_path=list(block.heading_path),
                page=block.page,
                bbox=block.bbox,
                media_ref=block.media_ref,
                confidence=block.confidence,
            )
            for block in document.blocks
        ],
        created_at=artifact.created_at,
    )


def _source_understanding_response(
    understanding: SourceUnderstanding | None,
) -> KnowledgeSourceUnderstandingResponse | None:
    if understanding is None:
        return None
    return KnowledgeSourceUnderstandingResponse(
        understanding_id=understanding.understanding_id,
        artifact_id=understanding.artifact_id,
        source_revision=understanding.source_revision,
        title=understanding.title,
        summary=understanding.summary,
        sections=[
            KnowledgeUnderstandingSectionResponse(
                title=section.title,
                block_ids=list(section.block_ids),
            )
            for section in understanding.sections
        ],
        topics=list(understanding.topics),
        block_kind_counts=dict(understanding.block_kind_counts),
        citations=[
            KnowledgeUnderstandingCitationResponse(
                block_id=citation.block_id,
                page=citation.page,
                heading_path=list(citation.heading_path),
            )
            for citation in understanding.citations
        ],
        generator_id=understanding.generator_id,
        generator_version=understanding.generator_version,
    )


def _workspace_synthesis_response(
    synthesis: WorkspaceSynthesis | None,
) -> KnowledgeWorkspaceSynthesisResponse | None:
    if synthesis is None:
        return None
    return KnowledgeWorkspaceSynthesisResponse(
        synthesis_id=synthesis.synthesis_id,
        input_hash=synthesis.input_hash,
        generator_id=synthesis.generator_id,
        generator_version=synthesis.generator_version,
        sources=[
            KnowledgeSynthesisSourceResponse(
                page_id=item.page_id,
                page_revision=item.page_revision,
                proposal_id=item.proposal_id,
                understanding_id=item.understanding_id,
                source_revision=item.source_revision,
                title=item.title,
                path=item.path,
                summary=item.summary,
                topics=list(item.topics),
                citation_block_ids=list(item.citation_block_ids),
            )
            for item in synthesis.sources
        ],
    )


def _page_response(page: KnowledgePage) -> KnowledgePageResponse:
    return KnowledgePageResponse.model_validate(
        {
            "page_id": page.page_id,
            "path": page.path,
            "title": page.title,
            "current_revision": page.current_revision,
            "updated_at": page.updated_at,
            "revisions": [
                {
                    "revision_id": revision.revision_id,
                    "sequence": revision.sequence,
                    "content_hash": revision.content_hash,
                    "source_revision": revision.source_revision,
                    "proposal_id": revision.proposal_id,
                    "change_kind": revision.change_kind,
                    "git_commit": revision.git_commit,
                    "created_at": revision.created_at,
                }
                for revision in page.revisions
            ],
        }
    )
