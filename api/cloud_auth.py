"""V7 cloud authentication routes with server-side session ownership."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from api.schemas import (
    CloudCurrentUserResponse,
    CloudDevelopmentLoginRequest,
    CloudGitHubOAuthStartRequest,
    CloudGitHubOAuthStartResponse,
)
from core.cloud.auth.repository import CloudRepository, new_browser_session_token
from core.cloud.github import (
    GitHubOAuthService,
    InvalidOAuthTransaction,
    OAuthProviderError,
    OAuthRegistrationDenied,
)

router = APIRouter()
_SESSION_COOKIE = "sage_session"
_OAUTH_BINDING_COOKIE = "sage_oauth_binding"
_SESSION_TTL = timedelta(days=7)
_OAUTH_BINDING_TTL_SECONDS = 300


def _repository(request: Request) -> CloudRepository:
    repository = getattr(request.app.state, "cloud_repository", None)
    if not isinstance(repository, CloudRepository):
        raise HTTPException(status_code=503, detail="cloud control plane is unavailable")
    return repository


def _github_oauth(request: Request) -> GitHubOAuthService:
    service = getattr(request.app.state, "cloud_github_oauth_service", None)
    if not isinstance(service, GitHubOAuthService):
        raise HTTPException(status_code=503, detail="GitHub authentication is unavailable")
    return service


@router.post(
    "/api/v1/cloud/auth/github/start",
    response_model=CloudGitHubOAuthStartResponse,
)
async def start_github_oauth(
    payload: CloudGitHubOAuthStartRequest,
    request: Request,
    response: Response,
) -> CloudGitHubOAuthStartResponse:
    """Start OAuth from a POST so an invite never appears in browser URLs."""
    started = await _github_oauth(request).start(
        invite_code=payload.invite_code,
        return_to=payload.return_to,
    )
    response.set_cookie(
        key=_OAUTH_BINDING_COOKIE,
        value=started.browser_binding,
        httponly=True,
        secure=bool(getattr(request.app.state, "cloud_secure_cookies", False)),
        samesite="lax",
        max_age=_OAUTH_BINDING_TTL_SECONDS,
        path="/api/v1/cloud/auth/github/callback",
    )
    response.headers["Cache-Control"] = "no-store"
    return CloudGitHubOAuthStartResponse(authorization_url=started.authorization_url)


@router.get("/api/v1/cloud/auth/github/callback")
async def complete_github_oauth(
    request: Request,
    code: str = Query(default="", max_length=1000),
    state: str = Query(default="", max_length=1000),
    error: str = Query(default="", max_length=200),
) -> Response:
    """Exchange GitHub's code and issue a separate opaque Sage session."""
    if error or not code or not state:
        raise HTTPException(status_code=400, detail="GitHub authentication failed")
    try:
        completed = await _github_oauth(request).complete(
            code=code,
            state=state,
            browser_binding=request.cookies.get(_OAUTH_BINDING_COOKIE, ""),
        )
    except InvalidOAuthTransaction as exc:
        raise HTTPException(status_code=400, detail="OAuth transaction is invalid") from exc
    except OAuthRegistrationDenied as exc:
        raise HTTPException(status_code=403, detail="a valid invite is required") from exc
    except OAuthProviderError as exc:
        raise HTTPException(status_code=502, detail="GitHub authentication failed") from exc

    token = new_browser_session_token()
    await _repository(request).create_session(
        completed.user.user_id,
        token,
        expires_at=datetime.now(UTC) + _SESSION_TTL,
    )
    frontend_url = str(getattr(request.app.state, "cloud_frontend_url", "")).rstrip("/")
    response = RedirectResponse(f"{frontend_url}{completed.return_to}", status_code=303)
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=bool(getattr(request.app.state, "cloud_secure_cookies", False)),
        samesite="lax",
        max_age=int(_SESSION_TTL.total_seconds()),
        path="/",
    )
    response.delete_cookie(
        key=_OAUTH_BINDING_COOKIE,
        path="/api/v1/cloud/auth/github/callback",
        httponly=True,
        secure=bool(getattr(request.app.state, "cloud_secure_cookies", False)),
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/api/v1/cloud/me", response_model=CloudCurrentUserResponse)
async def get_cloud_current_user(
    request: Request, response: Response
) -> CloudCurrentUserResponse:
    """Return the user resolved from the HttpOnly server session cookie."""
    user = await _repository(request).authenticated_user(request.cookies.get(_SESSION_COOKIE, ""))
    if user is None:
        raise HTTPException(status_code=401, detail="cloud authentication is required")
    response.headers["Cache-Control"] = "no-store"
    payload = CloudCurrentUserResponse(
        user_id=user.user_id, email=user.email, display_name=user.display_name
    )
    return payload


@router.post("/api/v1/cloud/auth/dev/login", response_model=CloudCurrentUserResponse)
async def development_login(
    payload: CloudDevelopmentLoginRequest,
    request: Request,
    response: Response,
) -> CloudCurrentUserResponse:
    """Create a development-only session after a one-time invite is consumed."""
    if (
        getattr(request.app.state, "cloud_app_env", "") != "development"
        or not bool(getattr(request.app.state, "cloud_dev_login_enabled", False))
    ):
        raise HTTPException(status_code=404, detail="not found")
    response.headers["Cache-Control"] = "no-store"
    repository = _repository(request)
    try:
        user = await repository.get_or_create_identity(
            provider="development",
            provider_subject=payload.email.strip().lower(),
            email=payload.email,
            display_name=payload.display_name,
            invite_code=payload.invite_code,
            reject_existing_identity=True,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="a valid invite is required") from exc
    token = new_browser_session_token()
    await repository.create_session(
        user.user_id, token, expires_at=datetime.now(UTC) + _SESSION_TTL
    )
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=bool(getattr(request.app.state, "cloud_secure_cookies", False)),
        samesite="lax",
        max_age=int(_SESSION_TTL.total_seconds()),
        path="/",
    )
    return CloudCurrentUserResponse(
        user_id=user.user_id, email=user.email, display_name=user.display_name
    )


@router.post("/api/v1/cloud/auth/logout", status_code=204, response_class=Response)
async def logout_cloud_session(request: Request) -> Response:
    """Revoke the server record and clear the browser's cookie."""
    token = request.cookies.get(_SESSION_COOKIE, "")
    await _repository(request).revoke_session(token)
    response = Response(status_code=204)
    response.delete_cookie(
        key=_SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=bool(getattr(request.app.state, "cloud_secure_cookies", True)),
        samesite="lax",
    )
    return response
