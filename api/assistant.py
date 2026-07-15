"""Personal assistant home read-model route."""

from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response

from api.cloud_dependencies import SESSION_COOKIE
from api.schemas import AssistantHomeSummary
from core.assistant import AssistantHomeSummaryService, HomeIdentity, HomeProject
from core.cloud.auth.repository import CloudRepository
from core.coding.persistence import CodingSessionStore

router = APIRouter()


@router.get("/api/v1/assistant/home", response_model=AssistantHomeSummary)
async def get_assistant_home(request: Request, response: Response) -> AssistantHomeSummary:
    """Return a bounded owner-visible summary without calling an LLM."""
    cloud = getattr(request.app.state, "cloud_repository", None)
    app_env = str(getattr(request.app.state, "cloud_app_env", "development"))
    token = request.cookies.get(SESSION_COOKIE, "")
    user = None
    if token and isinstance(cloud, CloudRepository):
        user = await cloud.authenticated_user(token)
        if user is None:
            raise HTTPException(status_code=401, detail="cloud authentication is required")
    elif app_env == "production":
        raise HTTPException(status_code=401, detail="cloud authentication is required")

    identity = (
        HomeIdentity.cloud(user.user_id, user.display_name)
        if user is not None
        else HomeIdentity.local()
    )
    projects: tuple[HomeProject, ...] | None = None
    project_error = False
    if user is not None and isinstance(cloud, CloudRepository):
        try:
            records = await cloud.list_projects(user.user_id)
            projects = tuple(
                HomeProject(project_id=item.project_id, name=item.name) for item in records
            )
        except Exception:
            project_error = True

    storage_root = Path(request.app.state.coding_storage_root)
    service = AssistantHomeSummaryService(
        CodingSessionStore(storage_root / "sessions"), storage_root
    )
    summary = service.build(
        identity,
        projects=projects,
        project_error=project_error,
    )
    response.headers["Cache-Control"] = "no-store"
    return AssistantHomeSummary.model_validate(asdict(summary))
