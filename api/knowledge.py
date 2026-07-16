"""Local V7.2 Knowledge Workspace review and rollback routes."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Annotated, Any, Literal

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
    KnowledgeEvidenceResponse,
    KnowledgeGoalAlignmentResponse,
    KnowledgeGraphAnalysisSnapshotResponse,
    KnowledgeGraphCommunitiesResponse,
    KnowledgeGraphCommunityResponse,
    KnowledgeGraphEdgeResponse,
    KnowledgeGraphEvidenceResponse,
    KnowledgeGraphInsightResponse,
    KnowledgeGraphInsightsResponse,
    KnowledgeGraphNeighborhoodResponse,
    KnowledgeGraphNodeDetailResponse,
    KnowledgeGraphNodeMetricResponse,
    KnowledgeGraphNodeResponse,
    KnowledgeGraphResponse,
    KnowledgeGraphSnapshotResponse,
    KnowledgeGraphStatusResponse,
    KnowledgeIndexResponse,
    KnowledgeIngestRequest,
    KnowledgeJobEventResponse,
    KnowledgeJobEventsResponse,
    KnowledgeJobItemResponse,
    KnowledgeJobResponse,
    KnowledgeJobsResponse,
    KnowledgeLearningCapabilityResponse,
    KnowledgeLearningGoalResponse,
    KnowledgeLearningGoalUpdateRequest,
    KnowledgeLearningRequest,
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
    KnowledgeRetrievalResponse,
    KnowledgeRollbackRequest,
    KnowledgeSearchRequest,
    KnowledgeSourceRootSummary,
    KnowledgeSourceUnderstandingResponse,
    KnowledgeSyncChangeResponse,
    KnowledgeSyncPlanRequest,
    KnowledgeSyncPlanResponse,
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
    KnowledgeEvidenceError,
    KnowledgeGoalAlignment,
    KnowledgeGraphAnalysis,
    KnowledgeGraphAnalysisError,
    KnowledgeGraphAnalysisSnapshot,
    KnowledgeGraphCommunity,
    KnowledgeGraphEdge,
    KnowledgeGraphError,
    KnowledgeGraphInsight,
    KnowledgeGraphNeighborhood,
    KnowledgeGraphNode,
    KnowledgeGraphNodeMetric,
    KnowledgeGraphOverview,
    KnowledgeGraphSnapshot,
    KnowledgeIndexSummary,
    KnowledgeMigrationPlan,
    KnowledgeMigrationResult,
    KnowledgePage,
    KnowledgePolicyDecision,
    KnowledgeProjectionError,
    KnowledgeProposal,
    KnowledgeRetrievalBundle,
    KnowledgeStore,
    LearningCapability,
    LearningGoal,
    LearningGoalDefinition,
    LearningGoalError,
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
    KnowledgeSyncPlan,
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


@router.get("/api/v1/knowledge/index", response_model=KnowledgeIndexResponse)
async def get_knowledge_index(request: Request, response: Response) -> KnowledgeIndexResponse:
    store = _require_store(request)
    response.headers["Cache-Control"] = "no-store"
    return _index_response(store.index_summary())


@router.post("/api/v1/knowledge/index/rebuild", response_model=KnowledgeIndexResponse)
async def rebuild_knowledge_index(request: Request) -> KnowledgeIndexResponse:
    store = _require_store(request)
    return _index_response(await asyncio.to_thread(store.rebuild_index))


@router.get("/api/v1/knowledge/graph/status", response_model=KnowledgeGraphStatusResponse)
async def get_knowledge_graph_status(
    request: Request, response: Response
) -> KnowledgeGraphStatusResponse:
    snapshot = _require_store(request).graph_status()
    response.headers["Cache-Control"] = "no-store"
    return KnowledgeGraphStatusResponse(
        status=snapshot.status if snapshot is not None else "unbuilt",  # type: ignore[arg-type]
        snapshot=_graph_snapshot_response(snapshot) if snapshot is not None else None,
    )


@router.post(
    "/api/v1/knowledge/graph/rebuild",
    response_model=KnowledgeGraphSnapshotResponse,
)
async def rebuild_knowledge_graph(
    request: Request,
    response: Response,
    force: bool = Query(default=False),
) -> KnowledgeGraphSnapshotResponse:
    try:
        snapshot = await asyncio.to_thread(_require_store(request).rebuild_graph, force=force)
    except KnowledgeGraphError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return _graph_snapshot_response(snapshot)


@router.get("/api/v1/knowledge/goal", response_model=KnowledgeLearningGoalResponse)
async def get_knowledge_learning_goal(
    request: Request, response: Response
) -> KnowledgeLearningGoalResponse:
    try:
        goal = await asyncio.to_thread(_require_store(request).learning_goal)
    except LearningGoalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return _learning_goal_response(goal)


@router.put("/api/v1/knowledge/goal", response_model=KnowledgeLearningGoalResponse)
async def update_knowledge_learning_goal(
    payload: KnowledgeLearningGoalUpdateRequest,
    request: Request,
    response: Response,
) -> KnowledgeLearningGoalResponse:
    definition = LearningGoalDefinition(
        goal_id=payload.goal_id,
        title=payload.title,
        description=payload.description,
        capabilities=tuple(
            LearningCapability(
                capability_id=item.capability_id,
                label=item.label,
                description=item.description,
                keywords=tuple(item.keywords),
                weight=item.weight,
                required=item.required,
            )
            for item in payload.capabilities
        ),
    )
    try:
        goal = await asyncio.to_thread(
            _require_store(request).update_learning_goal,
            definition,
            expected_goal_revision=payload.expected_goal_revision,
        )
    except LearningGoalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KnowledgeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return _learning_goal_response(goal)


@router.get(
    "/api/v1/knowledge/graph/communities",
    response_model=KnowledgeGraphCommunitiesResponse,
)
async def get_knowledge_graph_communities(
    request: Request,
    response: Response,
    graph_revision: str | None = Query(default=None, min_length=1, max_length=96),
) -> KnowledgeGraphCommunitiesResponse:
    try:
        analysis = await asyncio.to_thread(
            _require_store(request).analyze_graph, graph_revision=graph_revision
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge graph revision not found") from exc
    except (KnowledgeGraphError, KnowledgeGraphAnalysisError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LearningGoalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "private, no-cache"
    return _graph_communities_response(analysis)


@router.get(
    "/api/v1/knowledge/graph/insights",
    response_model=KnowledgeGraphInsightsResponse,
)
async def get_knowledge_graph_insights(
    request: Request,
    response: Response,
    graph_revision: str | None = Query(default=None, min_length=1, max_length=96),
    kind: Annotated[
        Literal[
            "missing_concept",
            "isolated_node",
            "bridge_node",
            "sparse_community",
            "capability_gap",
        ]
        | None,
        Query(),
    ] = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> KnowledgeGraphInsightsResponse:
    store = _require_store(request)
    try:
        analysis = await asyncio.to_thread(store.analyze_graph, graph_revision=graph_revision)
        goal = await asyncio.to_thread(store.learning_goal)
        if goal.goal_revision != analysis.snapshot.goal_revision:
            analysis = await asyncio.to_thread(store.analyze_graph, graph_revision=graph_revision)
            goal = await asyncio.to_thread(store.learning_goal)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge graph revision not found") from exc
    except (KnowledgeGraphError, KnowledgeGraphAnalysisError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LearningGoalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    selected = [item for item in analysis.insights if kind is None or item.kind == kind][:limit]
    response.headers["Cache-Control"] = "private, no-cache"
    return _graph_insights_response(analysis, goal, selected)


@router.post(
    "/api/v1/knowledge/graph/analysis/rebuild",
    response_model=KnowledgeGraphAnalysisSnapshotResponse,
)
async def rebuild_knowledge_graph_analysis(
    request: Request,
    response: Response,
    graph_revision: str | None = Query(default=None, min_length=1, max_length=96),
) -> KnowledgeGraphAnalysisSnapshotResponse:
    try:
        analysis = await asyncio.to_thread(
            _require_store(request).analyze_graph,
            graph_revision=graph_revision,
            force=True,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge graph revision not found") from exc
    except (KnowledgeGraphError, KnowledgeGraphAnalysisError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LearningGoalError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return _graph_analysis_snapshot_response(analysis.snapshot)


@router.get("/api/v1/knowledge/graph", response_model=KnowledgeGraphResponse)
async def get_knowledge_graph(
    request: Request,
    response: Response,
    graph_revision: str | None = Query(default=None, min_length=1, max_length=96),
    q: str = Query(default="", max_length=200),
    kind: Annotated[
        list[Literal["page", "source", "project", "concept", "decision", "tool"]] | None,
        Query(),
    ] = None,
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=500, ge=1, le=1_000),
    edge_limit: int = Query(default=1_000, ge=1, le=2_000),
) -> Any:
    try:
        overview = await asyncio.to_thread(
            _require_store(request).graph_overview,
            graph_revision=graph_revision,
            query=q,
            kinds=tuple(kind or ()),
            offset=offset,
            limit=limit,
            edge_limit=edge_limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge graph revision not found") from exc
    except KnowledgeGraphError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    etag = _graph_etag(
        overview.snapshot.graph_revision,
        q,
        ",".join(kind or ()),
        str(offset),
        str(limit),
        str(edge_limit),
    )
    headers = {"Cache-Control": "private, no-cache", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    for name, value in headers.items():
        response.headers[name] = value
    return _graph_overview_response(overview)


@router.get(
    "/api/v1/knowledge/graph/nodes/{node_id}",
    response_model=KnowledgeGraphNodeDetailResponse,
)
async def get_knowledge_graph_node(
    node_id: str,
    request: Request,
    response: Response,
    graph_revision: str | None = Query(default=None, min_length=1, max_length=96),
) -> KnowledgeGraphNodeDetailResponse:
    try:
        snapshot, node = await asyncio.to_thread(
            _require_store(request).graph_node,
            node_id,
            graph_revision=graph_revision,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge graph node not found") from exc
    except KnowledgeGraphError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "private, no-cache"
    return KnowledgeGraphNodeDetailResponse(
        snapshot=_graph_snapshot_response(snapshot),
        node=_graph_node_response(node),
    )


@router.get(
    "/api/v1/knowledge/graph/nodes/{node_id}/neighbors",
    response_model=KnowledgeGraphNeighborhoodResponse,
)
async def get_knowledge_graph_neighbors(
    node_id: str,
    request: Request,
    response: Response,
    graph_revision: str | None = Query(default=None, min_length=1, max_length=96),
    limit: int = Query(default=100, ge=1, le=500),
) -> KnowledgeGraphNeighborhoodResponse:
    try:
        neighborhood = await asyncio.to_thread(
            _require_store(request).graph_neighborhood,
            node_id,
            graph_revision=graph_revision,
            limit=limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge graph node not found") from exc
    except KnowledgeGraphError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "private, no-cache"
    return _graph_neighborhood_response(neighborhood)


@router.post("/api/v1/knowledge/search", response_model=KnowledgeRetrievalResponse)
async def search_knowledge(
    payload: KnowledgeSearchRequest,
    request: Request,
    response: Response,
) -> KnowledgeRetrievalResponse:
    """Return revision-bound evidence without exposing configured filesystem roots."""

    store = _require_store(request)
    try:
        bundle = await asyncio.to_thread(
            store.retrieve,
            payload.query,
            top_k=payload.top_k,
            token_budget=payload.token_budget,
            visibility=payload.visibility,
            source_ids=tuple(payload.source_ids),
            page_revisions=tuple(payload.page_revisions),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return _retrieval_response(bundle)


@router.post(
    "/api/v1/knowledge/learnings",
    response_model=KnowledgeProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_knowledge_learning(
    payload: KnowledgeLearningRequest,
    request: Request,
) -> KnowledgeProposalResponse:
    """Create a reversible, extractive Wiki revision from current citations."""

    store = _require_store(request)
    try:
        proposal = await asyncio.to_thread(
            store.propose_evidence_learning,
            payload.topic,
            tuple(payload.citation_ids),
            session_id=payload.session_id,
            run_id=payload.run_id,
            event_id=payload.event_id,
        )
    except KnowledgeEvidenceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _proposal_response(store, proposal)


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
        job = await service.create_batch(
            payload.source_root_id,
            payload.relative_directory,
            sync_plan_id=payload.sync_plan_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge source root not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="knowledge source directory not found") from exc
    except KnowledgeScanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KnowledgeJobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _job_response(job)


@router.post(
    "/api/v1/knowledge/sync/plan",
    response_model=KnowledgeSyncPlanResponse,
)
async def preview_knowledge_sync(
    payload: KnowledgeSyncPlanRequest, request: Request
) -> KnowledgeSyncPlanResponse:
    service = _require_job_service(request)
    try:
        plan = await service.preview_sync(payload.source_root_id, payload.relative_directory)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="knowledge source root not found") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="knowledge source directory not found") from exc
    except KnowledgeScanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KnowledgeJobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _sync_plan_response(plan)


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
        sync_plan_id=job.sync_plan_id,
        items=[_job_item_response(item) for item in items or []],
    )


def _job_item_response(item: KnowledgeJobItem) -> KnowledgeJobItemResponse:
    return KnowledgeJobItemResponse(
        item_id=item.item_id,
        job_id=item.job_id,
        relative_path=item.relative_path,
        source_revision=item.source_revision,
        change_kind=item.change_kind,  # type: ignore[arg-type]
        status=item.status,
        attempts=item.attempts,
        max_attempts=item.max_attempts,
        proposal_id=item.proposal_id,
        error=item.error,
        next_attempt_at=(item.next_attempt_at.isoformat() if item.next_attempt_at else None),
        updated_at=item.updated_at.isoformat(),
    )


def _sync_plan_response(plan: KnowledgeSyncPlan) -> KnowledgeSyncPlanResponse:
    visible_changes = plan.changes[:200]
    return KnowledgeSyncPlanResponse(
        plan_id=plan.plan_id,
        workspace_id=plan.workspace_id,
        source_root_id=plan.source_root_id,
        relative_directory=plan.relative_directory,
        pipeline_version=plan.pipeline_version,
        base_watermark=plan.base_watermark,
        target_watermark=plan.target_watermark,
        manifest_hash=plan.manifest_hash,
        status=plan.status,
        added_count=sum(item.change_kind == "added" for item in plan.changes),
        modified_count=sum(item.change_kind == "modified" for item in plan.changes),
        deleted_count=sum(item.change_kind == "deleted" for item in plan.changes),
        total_count=len(plan.changes),
        has_more=len(plan.changes) > len(visible_changes),
        changes=[
            KnowledgeSyncChangeResponse(
                relative_path=item.relative_path,
                change_kind=item.change_kind,
                previous_revision=item.previous_revision,
                source_revision=item.source_revision,
                idempotency_key=item.idempotency_key,
            )
            for item in visible_changes
        ],
        created_at=plan.created_at.isoformat(),
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


def _index_response(summary: KnowledgeIndexSummary) -> KnowledgeIndexResponse:
    return KnowledgeIndexResponse(
        status=("degraded" if summary.error_count else "ready"),
        backend=summary.backend,
        embedding_model=summary.embedding_model,
        embedding_revision=summary.embedding_revision,
        revision_count=summary.revision_count,
        indexed_revision_count=summary.indexed_revision_count,
        active_chunk_count=summary.active_chunk_count,
        total_chunk_count=summary.total_chunk_count,
        error_count=summary.error_count,
    )


def _graph_snapshot_response(
    snapshot: KnowledgeGraphSnapshot,
) -> KnowledgeGraphSnapshotResponse:
    return KnowledgeGraphSnapshotResponse.model_validate(
        {
            "graph_revision": snapshot.graph_revision,
            "workspace_id": snapshot.workspace_id,
            "wiki_watermark": snapshot.wiki_watermark,
            "projector_id": snapshot.projector_id,
            "projector_version": snapshot.projector_version,
            "config_hash": snapshot.config_hash,
            "status": snapshot.status,
            "node_count": snapshot.node_count,
            "edge_count": snapshot.edge_count,
            "warning_count": snapshot.warning_count,
            "error": snapshot.error,
            "created_at": snapshot.created_at,
            "completed_at": snapshot.completed_at,
            "stale": snapshot.stale,
        }
    )


def _learning_goal_response(goal: LearningGoal) -> KnowledgeLearningGoalResponse:
    return KnowledgeLearningGoalResponse(
        schema_version=goal.schema_version,
        goal_id=goal.goal_id,
        title=goal.title,
        description=goal.description,
        capabilities=[
            KnowledgeLearningCapabilityResponse(
                capability_id=item.capability_id,
                label=item.label,
                description=item.description,
                keywords=list(item.keywords),
                weight=item.weight,
                required=item.required,
            )
            for item in goal.capabilities
        ],
        goal_revision=goal.goal_revision,
        git_commit=goal.git_commit,
        structured=goal.structured,
    )


def _graph_analysis_snapshot_response(
    snapshot: KnowledgeGraphAnalysisSnapshot,
) -> KnowledgeGraphAnalysisSnapshotResponse:
    return KnowledgeGraphAnalysisSnapshotResponse.model_validate(
        {
            "analysis_revision": snapshot.analysis_revision,
            "workspace_id": snapshot.workspace_id,
            "graph_revision": snapshot.graph_revision,
            "goal_revision": snapshot.goal_revision,
            "algorithm_id": snapshot.algorithm_id,
            "algorithm_version": snapshot.algorithm_version,
            "seed": snapshot.seed,
            "resolution": snapshot.resolution,
            "threshold": snapshot.threshold,
            "status": snapshot.status,
            "community_count": snapshot.community_count,
            "insight_count": snapshot.insight_count,
            "error": snapshot.error,
            "created_at": snapshot.created_at,
            "completed_at": snapshot.completed_at,
        }
    )


def _graph_community_response(
    community: KnowledgeGraphCommunity,
) -> KnowledgeGraphCommunityResponse:
    return KnowledgeGraphCommunityResponse(
        community_id=community.community_id,
        label=community.label,
        node_count=community.node_count,
        edge_count=community.edge_count,
        cohesion=community.cohesion,
        properties=community.properties,
    )


def _graph_metric_response(metric: KnowledgeGraphNodeMetric) -> KnowledgeGraphNodeMetricResponse:
    return KnowledgeGraphNodeMetricResponse(
        node_id=metric.node_id,
        community_id=metric.community_id,
        degree=metric.degree,
        weighted_degree=metric.weighted_degree,
        bridge_score=metric.bridge_score,
    )


def _goal_alignment_response(alignment: KnowledgeGoalAlignment) -> KnowledgeGoalAlignmentResponse:
    return KnowledgeGoalAlignmentResponse.model_validate(
        {
            "capability_id": alignment.capability_id,
            "label": alignment.label,
            "coverage": alignment.coverage,
            "status": alignment.status,
            "matched_keywords": list(alignment.matched_keywords),
            "missing_keywords": list(alignment.missing_keywords),
            "matched_node_ids": list(alignment.matched_node_ids),
        }
    )


def _graph_insight_response(insight: KnowledgeGraphInsight) -> KnowledgeGraphInsightResponse:
    return KnowledgeGraphInsightResponse.model_validate(
        {
            "insight_id": insight.insight_id,
            "kind": insight.kind,
            "severity": insight.severity,
            "title": insight.title,
            "description": insight.description,
            "node_id": insight.node_id,
            "community_id": insight.community_id,
            "capability_id": insight.capability_id,
            "properties": insight.properties,
        }
    )


def _graph_communities_response(
    analysis: KnowledgeGraphAnalysis,
) -> KnowledgeGraphCommunitiesResponse:
    return KnowledgeGraphCommunitiesResponse(
        analysis=_graph_analysis_snapshot_response(analysis.snapshot),
        communities=[_graph_community_response(item) for item in analysis.communities],
        node_metrics=[_graph_metric_response(item) for item in analysis.node_metrics],
    )


def _graph_insights_response(
    analysis: KnowledgeGraphAnalysis,
    goal: LearningGoal,
    insights: list[KnowledgeGraphInsight],
) -> KnowledgeGraphInsightsResponse:
    return KnowledgeGraphInsightsResponse(
        analysis=_graph_analysis_snapshot_response(analysis.snapshot),
        goal=_learning_goal_response(goal),
        alignments=[_goal_alignment_response(item) for item in analysis.alignments],
        insights=[_graph_insight_response(item) for item in insights],
    )


def _graph_node_response(node: KnowledgeGraphNode) -> KnowledgeGraphNodeResponse:
    return KnowledgeGraphNodeResponse.model_validate(
        {
            "node_id": node.node_id,
            "kind": node.kind,
            "label": node.label,
            "page_id": node.page_id,
            "page_revision": node.page_revision,
            "source_id": node.source_id,
            "source_revision": node.source_revision,
            "properties": node.properties,
        }
    )


def _graph_edge_response(edge: KnowledgeGraphEdge) -> KnowledgeGraphEdgeResponse:
    return KnowledgeGraphEdgeResponse.model_validate(
        {
            "edge_id": edge.edge_id,
            "source_node_id": edge.source_node_id,
            "target_node_id": edge.target_node_id,
            "kind": edge.kind,
            "directed": edge.directed,
            "weight": edge.weight,
            "confidence": edge.confidence,
            "extractor_id": edge.extractor_id,
            "extractor_version": edge.extractor_version,
            "properties": edge.properties,
            "evidence": [
                KnowledgeGraphEvidenceResponse(
                    citation_id=item.citation_id,
                    chunk_id=item.chunk_id,
                    page_id=item.page_id,
                    page_revision=item.page_revision,
                    source_id=item.source_id,
                    source_revision=item.source_revision,
                )
                for item in edge.evidence
            ],
        }
    )


def _graph_overview_response(overview: KnowledgeGraphOverview) -> KnowledgeGraphResponse:
    return KnowledgeGraphResponse(
        snapshot=_graph_snapshot_response(overview.snapshot),
        nodes=[_graph_node_response(node) for node in overview.nodes],
        edges=[_graph_edge_response(edge) for edge in overview.edges],
        offset=overview.offset,
        next_offset=overview.next_offset,
        has_more=overview.has_more,
    )


def _graph_neighborhood_response(
    neighborhood: KnowledgeGraphNeighborhood,
) -> KnowledgeGraphNeighborhoodResponse:
    return KnowledgeGraphNeighborhoodResponse(
        snapshot=_graph_snapshot_response(neighborhood.snapshot),
        center=_graph_node_response(neighborhood.center),
        nodes=[_graph_node_response(node) for node in neighborhood.nodes],
        edges=[_graph_edge_response(edge) for edge in neighborhood.edges],
    )


def _graph_etag(*parts: str) -> str:
    payload = "\0".join(parts).encode("utf-8")
    return f'"{hashlib.sha256(payload).hexdigest()}"'


def _retrieval_response(bundle: KnowledgeRetrievalBundle) -> KnowledgeRetrievalResponse:
    citations: list[KnowledgeEvidenceResponse] = []
    for evidence in bundle.evidence:
        hit = evidence.hit
        chunk = hit.chunk
        citations.append(
            KnowledgeEvidenceResponse(
                citation_id=hit.citation_id,
                rank=hit.rank,
                rrf_score=hit.rrf_score,
                sparse_rank=hit.sparse_rank,
                sparse_score=hit.sparse_score,
                dense_rank=hit.dense_rank,
                dense_score=hit.dense_score,
                chunk_id=chunk.chunk_id,
                page_id=chunk.page_id,
                page_revision=chunk.page_revision,
                page_path=chunk.page_path,
                source_id=chunk.source_id,
                source_revision=chunk.source_revision,
                source_kind=chunk.source_kind,
                source_relative_path=chunk.source_relative_path,
                proposal_id=chunk.proposal_id,
                artifact_id=chunk.artifact_id,
                block_id=chunk.block_id,
                ordinal=chunk.ordinal,
                title=chunk.title,
                heading_path=list(chunk.heading_path),
                page_number=chunk.page_number,
                excerpt=evidence.excerpt,
                token_count=evidence.token_count,
                truncated=evidence.truncated,
            )
        )
    return KnowledgeRetrievalResponse(
        query=bundle.query,
        status=bundle.status,
        token_budget=bundle.token_budget,
        used_tokens=bundle.used_tokens,
        omitted_count=bundle.omitted_count,
        citations=citations,
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
