"""Canonicalize and authorize one run-frozen Chat Harness context."""

from __future__ import annotations

import hashlib
from pathlib import Path

from api.schemas import (
    HarnessOperationRef,
    HarnessResourceContext,
    HarnessSelectionContext,
    HarnessSurfaceContext,
)
from core.coding.memory import workspace_id_from_path
from core.coding.runtime import CodingRuntime
from core.knowledge import (
    KnowledgeGraphError,
    KnowledgeGraphNode,
    KnowledgePage,
    KnowledgeStore,
)
from core.knowledge.jobs import KnowledgeJobNotFoundError, KnowledgeJobService


class SurfaceContextValidationError(ValueError):
    """A caller-proposed context cannot be bound to the current session."""


async def validate_surface_context(
    context: HarnessSurfaceContext,
    *,
    runtime: CodingRuntime,
    knowledge_store: object | None,
    knowledge_job_service: object | None,
    app_env: str,
) -> HarnessSurfaceContext:
    """Return only server-canonical context or fail before model execution."""
    if context.surface == "coding":
        return _validate_coding_context(context, runtime)
    if context.surface == "knowledge":
        return await _validate_knowledge_context(
            context,
            knowledge_store=knowledge_store,
            knowledge_job_service=knowledge_job_service,
            app_env=app_env,
        )
    raise SurfaceContextValidationError("growth surface context is not available")


def _validate_coding_context(
    context: HarnessSurfaceContext, runtime: CodingRuntime
) -> HarnessSurfaceContext:
    expected_workspace = workspace_id_from_path(runtime.workspace.root)
    if context.workspace_id != expected_workspace:
        raise SurfaceContextValidationError("surface context workspace is not available")
    if context.graph_revision is not None:
        raise SurfaceContextValidationError("coding context cannot bind a graph revision")
    if context.operation_refs:
        raise SurfaceContextValidationError("coding operation references are not available")

    resource: HarnessResourceContext | None = None
    if context.resource is not None:
        if (
            context.resource.type != "coding_workspace"
            or context.resource.id != expected_workspace
            or context.resource.revision is not None
        ):
            raise SurfaceContextValidationError("coding workspace resource is invalid")
        resource = HarnessResourceContext(
            type="coding_workspace",
            id=expected_workspace,
            label=runtime.workspace.root.name or "workspace",
        )

    selection: HarnessSelectionContext | None = None
    if context.selection is not None:
        if context.selection.type != "coding_file":
            raise SurfaceContextValidationError("coding selection type is invalid")
        try:
            path = runtime.workspace.path(context.selection.id)
        except ValueError as exc:
            raise SurfaceContextValidationError("coding selection is outside the workspace") from exc
        if not path.is_file():
            raise SurfaceContextValidationError("coding selection does not exist")
        relative_path = runtime.workspace.relative(path)
        revision = context.selection.revision
        if revision is not None and revision != _file_revision(path):
            raise SurfaceContextValidationError("coding selection revision is stale")
        selection = HarnessSelectionContext(
            type="coding_file",
            id=relative_path,
            revision=revision,
            label=relative_path,
        )

    return HarnessSurfaceContext(
        surface="coding",
        workspace_id=expected_workspace,
        resource=resource,
        selection=selection,
        operation_refs=[],
    )


async def _validate_knowledge_context(
    context: HarnessSurfaceContext,
    *,
    knowledge_store: object | None,
    knowledge_job_service: object | None,
    app_env: str,
) -> HarnessSurfaceContext:
    if app_env == "production":
        raise SurfaceContextValidationError(
            "knowledge context requires tenant isolation before cloud use"
        )
    if not isinstance(knowledge_store, KnowledgeStore):
        raise SurfaceContextValidationError("knowledge workspace is not configured")
    workspace_id = knowledge_store.knowledge_index.workspace_id
    if context.workspace_id != workspace_id:
        raise SurfaceContextValidationError("surface context workspace is not available")

    page = None
    if context.resource is not None and context.resource.type == "knowledge_page":
        page = next(
            (item for item in knowledge_store.list_pages() if item.page_id == context.resource.id),
            None,
        )
        if page is None:
            raise SurfaceContextValidationError("knowledge page does not exist")
        if context.resource.revision != page.current_revision:
            raise SurfaceContextValidationError("knowledge page revision is stale")

    graph_snapshot = None
    graph_node = None
    if context.selection is not None and context.selection.type == "graph_node":
        if context.graph_revision is None:
            raise SurfaceContextValidationError("graph node context requires a graph revision")
        try:
            graph_snapshot, graph_node = knowledge_store.graph_node(
                context.selection.id,
                graph_revision=context.graph_revision,
            )
        except (KeyError, KnowledgeGraphError) as exc:
            raise SurfaceContextValidationError("knowledge graph selection is stale") from exc
        expected_revision = (
            graph_node.page_revision
            or graph_node.source_revision
            or graph_snapshot.graph_revision
        )
        if context.selection.revision != expected_revision:
            raise SurfaceContextValidationError("knowledge graph selection revision is stale")
    elif context.graph_revision is not None:
        snapshot = knowledge_store.graph_status()
        if snapshot is None or snapshot.graph_revision != context.graph_revision or snapshot.stale:
            raise SurfaceContextValidationError("knowledge graph revision is stale")

    resource = _canonical_knowledge_resource(context, page=page, graph_node=graph_node)
    selection = _canonical_knowledge_selection(
        context,
        page=page,
        graph_node=graph_node,
        graph_revision=graph_snapshot.graph_revision if graph_snapshot is not None else None,
    )
    operation_refs = await _validated_knowledge_operations(
        context.operation_refs,
        workspace_id=workspace_id,
        knowledge_job_service=knowledge_job_service,
    )
    return HarnessSurfaceContext(
        surface="knowledge",
        workspace_id=workspace_id,
        resource=resource,
        selection=selection,
        graph_revision=context.graph_revision,
        operation_refs=operation_refs,
    )


def _canonical_knowledge_resource(
    context: HarnessSurfaceContext,
    *,
    page: KnowledgePage | None,
    graph_node: KnowledgeGraphNode | None,
) -> HarnessResourceContext | None:
    resource = context.resource
    if resource is None:
        return None
    if resource.type == "coding_workspace":
        raise SurfaceContextValidationError("knowledge resource type is invalid")
    if resource.type == "knowledge_page":
        if page is None:
            raise SurfaceContextValidationError("knowledge page does not exist")
        if graph_node is not None and (
            graph_node.page_id != page.page_id
            or graph_node.page_revision != page.current_revision
        ):
            raise SurfaceContextValidationError("knowledge resource and selection do not match")
        return HarnessResourceContext(
            type="knowledge_page",
            id=page.page_id,
            revision=page.current_revision,
            label=page.title,
        )
    if graph_node is None or graph_node.source_id != resource.id:
        raise SurfaceContextValidationError("knowledge source and selection do not match")
    if resource.revision != graph_node.source_revision:
        raise SurfaceContextValidationError("knowledge source revision is stale")
    return HarnessResourceContext(
        type="knowledge_source",
        id=graph_node.source_id,
        revision=graph_node.source_revision,
        label=graph_node.label,
    )


def _canonical_knowledge_selection(
    context: HarnessSurfaceContext,
    *,
    page: KnowledgePage | None,
    graph_node: KnowledgeGraphNode | None,
    graph_revision: str | None,
) -> HarnessSelectionContext | None:
    selection = context.selection
    if selection is None:
        return None
    if selection.type == "coding_file":
        raise SurfaceContextValidationError("knowledge selection type is invalid")
    if selection.type == "graph_node":
        if graph_node is None:
            raise SurfaceContextValidationError("knowledge graph selection does not exist")
        revision = graph_node.page_revision or graph_node.source_revision or graph_revision
        return HarnessSelectionContext(
            type="graph_node",
            id=graph_node.node_id,
            revision=revision,
            label=graph_node.label,
        )
    if selection.type == "knowledge_page":
        selected_page = page
        if selected_page is None or selected_page.page_id != selection.id:
            raise SurfaceContextValidationError("knowledge page selection does not exist")
        if selection.revision != selected_page.current_revision:
            raise SurfaceContextValidationError("knowledge page selection revision is stale")
        return HarnessSelectionContext(
            type="knowledge_page",
            id=selected_page.page_id,
            revision=selected_page.current_revision,
            label=selected_page.title,
        )
    if graph_node is None or graph_node.source_id != selection.id:
        raise SurfaceContextValidationError("knowledge source selection does not exist")
    if selection.revision != graph_node.source_revision:
        raise SurfaceContextValidationError("knowledge source selection revision is stale")
    return HarnessSelectionContext(
        type="knowledge_source",
        id=graph_node.source_id,
        revision=graph_node.source_revision,
        label=graph_node.label,
    )


async def _validated_knowledge_operations(
    operation_refs: list[HarnessOperationRef],
    *,
    workspace_id: str,
    knowledge_job_service: object | None,
) -> list[HarnessOperationRef]:
    if not operation_refs:
        return []
    if not isinstance(knowledge_job_service, KnowledgeJobService):
        raise SurfaceContextValidationError("knowledge jobs are not configured")
    validated: list[HarnessOperationRef] = []
    seen: set[tuple[str, str]] = set()
    for operation in operation_refs:
        if operation.kind != "knowledge_job":
            raise SurfaceContextValidationError("knowledge operation type is invalid")
        key = (operation.kind, operation.id)
        if key in seen:
            continue
        try:
            job = await knowledge_job_service.repository.get_job(operation.id)
        except KnowledgeJobNotFoundError as exc:
            raise SurfaceContextValidationError("knowledge job does not exist") from exc
        if job.workspace_id != workspace_id:
            raise SurfaceContextValidationError("knowledge job belongs to another workspace")
        validated.append(operation)
        seen.add(key)
    return validated


def _file_revision(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
