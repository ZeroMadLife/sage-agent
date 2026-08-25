"""Shared cloud authorization dependencies."""

from fastapi import HTTPException, Request, WebSocketException, status
from starlette.requests import HTTPConnection

from core.cloud.auth.models import CloudUser
from core.cloud.auth.repository import CloudRepository
from core.cloud.auth.tokens import InvalidAccessToken, decode_access_token

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


async def authenticated_connection_user(connection: HTTPConnection) -> CloudUser | None:
    """Resolve a browser cookie or Bearer token without raising on absence."""
    repository = getattr(connection.app.state, "cloud_repository", None)
    is_websocket = connection.scope.get("type") == "websocket"
    if not isinstance(repository, CloudRepository):
        if is_websocket:
            raise WebSocketException(
                code=status.WS_1011_INTERNAL_ERROR,
                reason="cloud control plane is unavailable",
            )
        raise HTTPException(status_code=503, detail="cloud control plane is unavailable")
    authorization = connection.headers.get("authorization", "")
    user = None
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        try:
            claims = decode_access_token(
                token,
                secret=str(getattr(connection.app.state, "cloud_token_secret", "")),
            )
        except InvalidAccessToken:
            claims = None
        if claims is not None:
            user = await repository.authenticated_user_by_session_id(claims.session_id)
            if user is None or user.user_id != claims.user_id:
                user = None
    if user is None and not authorization.lower().startswith("bearer "):
        user = await repository.authenticated_user(connection.cookies.get(SESSION_COOKIE, ""))
    return user


async def require_authenticated_connection(connection: HTTPConnection) -> CloudUser:
    """Authenticate a browser cookie or a short-lived Bearer access token."""
    is_websocket = connection.scope.get("type") == "websocket"
    user = await authenticated_connection_user(connection)
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
