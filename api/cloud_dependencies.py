"""Shared cloud authorization dependencies."""

from fastapi import HTTPException, Request

from core.cloud.auth.models import CloudUser
from core.cloud.auth.repository import CloudRepository

SESSION_COOKIE = "sage_session"


def cloud_repository(request: Request) -> CloudRepository:
    """Return the configured control-plane repository or fail closed."""
    repository = getattr(request.app.state, "cloud_repository", None)
    if not isinstance(repository, CloudRepository):
        raise HTTPException(status_code=503, detail="cloud control plane is unavailable")
    return repository


async def require_authenticated_user(request: Request) -> CloudUser:
    """Resolve the caller from a server-side session, never a user ID header."""
    user = await cloud_repository(request).authenticated_user(
        request.cookies.get(SESSION_COOKIE, "")
    )
    if user is None:
        raise HTTPException(status_code=401, detail="cloud authentication is required")
    return user
