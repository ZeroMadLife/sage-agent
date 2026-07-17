"""Owner-scoped V7 workspace metadata routes."""

from fastapi import APIRouter, HTTPException, Request

from api.cloud_dependencies import cloud_repository, require_authenticated_user
from api.schemas import (
    CloudProjectCreateRequest,
    CloudProjectResponse,
    CloudWorkspaceCreateRequest,
    CloudWorkspaceResponse,
)
from core.cloud.auth.models import CloudWorkspace

router = APIRouter()


@router.get("/api/v1/cloud/projects", response_model=list[CloudProjectResponse])
async def list_cloud_projects(request: Request) -> list[CloudProjectResponse]:
    """Authenticate before returning the caller's project list."""
    user = await require_authenticated_user(request)
    projects = await cloud_repository(request).list_projects(user.user_id)
    return [
        CloudProjectResponse(project_id=project.project_id, name=project.name)
        for project in projects
    ]


@router.post("/api/v1/cloud/projects", response_model=CloudProjectResponse)
async def create_cloud_project(
    payload: CloudProjectCreateRequest, request: Request
) -> CloudProjectResponse:
    """Create a project owned by the authenticated user."""
    user = await require_authenticated_user(request)
    project = await cloud_repository(request).create_project(user.user_id, payload.name)
    return CloudProjectResponse(project_id=project.project_id, name=project.name)


@router.post(
    "/api/v1/cloud/projects/{project_id}/workspaces",
    response_model=CloudWorkspaceResponse,
)
async def create_cloud_workspace(
    project_id: str, payload: CloudWorkspaceCreateRequest, request: Request
) -> CloudWorkspaceResponse:
    """Create metadata only after owner access to the project is confirmed."""
    user = await require_authenticated_user(request)
    project = await cloud_repository(request).authenticated_project(user.user_id, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="workspace project was not found")
    workspace = await cloud_repository(request).create_workspace(
        project.project_id, provider=payload.provider
    )
    return _workspace_response(workspace)


@router.get(
    "/api/v1/cloud/workspaces/{workspace_id}",
    response_model=CloudWorkspaceResponse,
)
async def get_cloud_workspace(workspace_id: str, request: Request) -> CloudWorkspaceResponse:
    """Return one workspace only when its owner matches the current session."""
    user = await require_authenticated_user(request)
    workspace = await cloud_repository(request).authenticated_workspace(user.user_id, workspace_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="workspace was not found")
    return _workspace_response(workspace)


def _workspace_response(workspace: CloudWorkspace) -> CloudWorkspaceResponse:
    return CloudWorkspaceResponse(
        workspace_id=workspace.workspace_id,
        project_id=workspace.project_id,
        provider=workspace.provider,
        lifecycle_state=workspace.lifecycle_state,
    )
