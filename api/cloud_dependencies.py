"""Shared cloud authorization dependencies."""

from fastapi import HTTPException, Request, WebSocketException, status
from starlette.requests import HTTPConnection

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
    return await require_authenticated_connection(request)


async def require_authenticated_connection(connection: HTTPConnection) -> CloudUser:
    """Authenticate an HTTP or WebSocket connection with the server session."""
    repository = getattr(connection.app.state, "cloud_repository", None)
    is_websocket = connection.scope.get("type") == "websocket"
    if not isinstance(repository, CloudRepository):
        if is_websocket:
            raise WebSocketException(
                code=status.WS_1011_INTERNAL_ERROR,
                reason="cloud control plane is unavailable",
            )
        raise HTTPException(status_code=503, detail="cloud control plane is unavailable")
    user = await repository.authenticated_user(connection.cookies.get(SESSION_COOKIE, ""))
    if user is None:
        if is_websocket:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="cloud authentication is required",
            )
        raise HTTPException(status_code=401, detail="cloud authentication is required")
    return user


async def require_cloud_authentication_in_production(
    connection: HTTPConnection,
) -> None:
    """Require cloud authentication only when the app runs in production."""
    app_env = str(getattr(connection.app.state, "cloud_app_env", "development")).lower()
    if app_env == "production":
        await require_authenticated_connection(connection)
