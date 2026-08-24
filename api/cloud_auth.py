"""V7 cloud authentication routes with server-side session ownership."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse

from api.schemas import (
    CloudAuthOptionsResponse,
    CloudCanaryInviteLoginRequest,
    CloudCurrentUserResponse,
    CloudDevelopmentLoginRequest,
    CloudDeviceLoginRequest,
    CloudGitHubOAuthStartRequest,
    CloudGitHubOAuthStartResponse,
    CloudRefreshTokenRequest,
    CloudTokenResponse,
    CloudTokenRevokeRequest,
)
from core.cloud.auth.models import CloudUser
from core.cloud.auth.repository import (
    CloudRepository,
    DeviceLimitReached,
    RefreshTokenReuseDetected,
    new_browser_session_token,
)
from core.cloud.auth.tokens import (
    InvalidAccessToken,
    decode_access_token,
    encode_access_token,
    new_refresh_token,
)
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
_CANARY_SESSION_TTL = timedelta(days=30)
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


def _set_session_cookie(response: Response, request: Request, token: str, ttl: timedelta) -> None:
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=bool(getattr(request.app.state, "cloud_secure_cookies", False)),
        samesite="lax",
        max_age=int(ttl.total_seconds()),
        path="/",
    )


def _set_token_response_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


async def _issue_api_tokens(
    request: Request,
    *,
    user: CloudUser,
    session_id: str,
) -> CloudTokenResponse:
    """Create a short-lived JWT and persist its rotating refresh companion."""
    access_ttl = int(getattr(request.app.state, "cloud_access_token_ttl_seconds", 900))
    refresh_ttl_days = int(getattr(request.app.state, "cloud_refresh_token_ttl_days", 30))
    refresh = new_refresh_token()
    await _repository(request).create_refresh_token(
        user_id=user.user_id,
        family_id=session_id,
        token=refresh,
        expires_at=datetime.now(UTC) + timedelta(days=refresh_ttl_days),
    )
    access = encode_access_token(
        user_id=user.user_id,
        session_id=session_id,
        secret=str(getattr(request.app.state, "cloud_token_secret", "")),
        ttl_seconds=access_ttl,
    )
    return CloudTokenResponse(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        access_token=access,
        expires_in=access_ttl,
        refresh_token=refresh,
    )


@router.get("/api/v1/cloud/auth/options", response_model=CloudAuthOptionsResponse)
async def get_cloud_auth_options(request: Request, response: Response) -> CloudAuthOptionsResponse:
    """Expose login capabilities without leaking credentials or account state."""
    response.headers["Cache-Control"] = "no-store"
    return CloudAuthOptionsResponse(
        canary_invite_login=bool(
            getattr(request.app.state, "cloud_canary_invite_login_enabled", False)
        ),
        github_login=isinstance(
            getattr(request.app.state, "cloud_github_oauth_service", None),
            GitHubOAuthService,
        ),
    )


@router.post(
    "/api/v1/cloud/auth/canary/login",
    response_model=CloudCurrentUserResponse,
)
async def canary_invite_login(
    payload: CloudCanaryInviteLoginRequest,
    request: Request,
    response: Response,
) -> CloudCurrentUserResponse:
    """Exchange one private-Canary invite for a revocable 30-day device session."""
    if not bool(getattr(request.app.state, "cloud_canary_invite_login_enabled", False)):
        raise HTTPException(status_code=404, detail="not found")
    token = new_browser_session_token()
    try:
        user, _ = await _repository(request).create_canary_invite_session(
            invite_code=payload.invite_code,
            token=token,
            device_name=payload.device_name,
            expires_at=datetime.now(UTC) + _CANARY_SESSION_TTL,
        )
    except DeviceLimitReached as exc:
        raise HTTPException(status_code=409, detail="最多允许 3 台设备保持登录") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="邀请码无效、已使用或账号已停用") from exc
    _set_session_cookie(response, request, token, _CANARY_SESSION_TTL)
    response.headers["Cache-Control"] = "no-store"
    return CloudCurrentUserResponse(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
    )


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
    try:
        await _repository(request).create_session(
            completed.user.user_id,
            token,
            expires_at=datetime.now(UTC) + _SESSION_TTL,
        )
    except DeviceLimitReached as exc:
        raise HTTPException(status_code=409, detail="最多允许 3 台设备保持登录") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="账号已停用") from exc
    frontend_url = str(getattr(request.app.state, "cloud_frontend_url", "")).rstrip("/")
    response = RedirectResponse(f"{frontend_url}{completed.return_to}", status_code=303)
    _set_session_cookie(response, request, token, _SESSION_TTL)
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
async def get_cloud_current_user(request: Request, response: Response) -> CloudCurrentUserResponse:
    """Return the user resolved from either the browser or client session."""
    from api.cloud_dependencies import require_authenticated_connection

    user = await require_authenticated_connection(request)
    response.headers["Cache-Control"] = "no-store"
    payload = CloudCurrentUserResponse(
        user_id=user.user_id, email=user.email, display_name=user.display_name
    )
    return payload


@router.post("/api/v1/cloud/auth/device/login", response_model=CloudTokenResponse)
async def device_login(
    payload: CloudDeviceLoginRequest,
    request: Request,
    response: Response,
) -> CloudTokenResponse:
    """Exchange a private invite for tokens intended for TUI/desktop clients."""
    repository = _repository(request)
    app_env = str(getattr(request.app.state, "cloud_app_env", "development")).lower()
    try:
        if app_env == "development" and bool(
            getattr(request.app.state, "cloud_dev_login_enabled", False)
        ):
            if not payload.email:
                raise HTTPException(status_code=422, detail="email is required in development")
            user = await repository.get_or_create_identity(
                provider="development",
                provider_subject=payload.email,
                email=payload.email,
                display_name=payload.display_name,
                invite_code=payload.invite_code,
                reject_existing_identity=True,
            )
            opaque = new_browser_session_token()
            session = await repository.create_session(
                user.user_id,
                opaque,
                device_name=payload.device_name,
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        elif bool(getattr(request.app.state, "cloud_canary_invite_login_enabled", False)):
            opaque = new_browser_session_token()
            user, session = await repository.create_canary_invite_session(
                invite_code=payload.invite_code,
                token=opaque,
                device_name=payload.device_name,
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
        else:
            raise HTTPException(status_code=404, detail="not found")
    except DeviceLimitReached as exc:
        raise HTTPException(status_code=409, detail="最多允许 3 台设备保持登录") from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="邀请码无效、已使用或账号已停用") from exc
    _set_token_response_headers(response)
    return await _issue_api_tokens(request, user=user, session_id=session.session_id)


@router.post("/api/v1/cloud/auth/token/refresh", response_model=CloudTokenResponse)
async def refresh_cloud_token(
    payload: CloudRefreshTokenRequest,
    request: Request,
    response: Response,
) -> CloudTokenResponse:
    """Rotate a refresh token exactly once and issue a new access token."""
    replacement = new_refresh_token()
    try:
        rotated = await _repository(request).rotate_refresh_token(
            token=payload.refresh_token,
            replacement=replacement,
            expires_at=datetime.now(UTC)
            + timedelta(days=int(getattr(request.app.state, "cloud_refresh_token_ttl_days", 30))),
        )
    except RefreshTokenReuseDetected as exc:
        raise HTTPException(status_code=401, detail="refresh token reuse detected") from exc
    if rotated is None:
        raise HTTPException(status_code=401, detail="refresh token is invalid or expired")
    user_id, session_id = rotated
    user = await _repository(request).authenticated_user_by_session_id(session_id)
    if user is None or user.user_id != user_id:
        raise HTTPException(status_code=401, detail="cloud authentication is required")
    access_ttl = int(getattr(request.app.state, "cloud_access_token_ttl_seconds", 900))
    access = encode_access_token(
        user_id=user.user_id,
        session_id=session_id,
        secret=str(getattr(request.app.state, "cloud_token_secret", "")),
        ttl_seconds=access_ttl,
    )
    _set_token_response_headers(response)
    return CloudTokenResponse(
        user_id=user.user_id,
        email=user.email,
        display_name=user.display_name,
        access_token=access,
        expires_in=access_ttl,
        refresh_token=replacement,
    )


@router.post("/api/v1/cloud/auth/token/revoke", status_code=204, response_class=Response)
async def revoke_cloud_token(
    payload: CloudTokenRevokeRequest,
    request: Request,
) -> Response:
    """Revoke a refresh-token family and its server-side device session."""
    await _repository(request).revoke_refresh_token(payload.refresh_token)
    return Response(status_code=204)


@router.post("/api/v1/cloud/auth/dev/login", response_model=CloudCurrentUserResponse)
async def development_login(
    payload: CloudDevelopmentLoginRequest,
    request: Request,
    response: Response,
) -> CloudCurrentUserResponse:
    """Create a development-only session after a one-time invite is consumed."""
    if getattr(request.app.state, "cloud_app_env", "") != "development" or not bool(
        getattr(request.app.state, "cloud_dev_login_enabled", False)
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
    try:
        await repository.create_session(
            user.user_id, token, expires_at=datetime.now(UTC) + _SESSION_TTL
        )
    except DeviceLimitReached as exc:
        raise HTTPException(status_code=409, detail="最多允许 3 台设备保持登录") from exc
    _set_session_cookie(response, request, token, _SESSION_TTL)
    return CloudCurrentUserResponse(
        user_id=user.user_id, email=user.email, display_name=user.display_name
    )


@router.post("/api/v1/cloud/auth/logout", status_code=204, response_class=Response)
async def logout_cloud_session(request: Request) -> Response:
    """Revoke the server record and clear the browser's cookie."""
    token = request.cookies.get(_SESSION_COOKIE, "")
    await _repository(request).revoke_session(token)
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        try:
            claims = decode_access_token(
                authorization[7:].strip(),
                secret=str(getattr(request.app.state, "cloud_token_secret", "")),
            )
        except InvalidAccessToken:
            claims = None
        if claims is not None:
            await _repository(request).revoke_session_by_id(claims.session_id)
    response = Response(status_code=204)
    response.delete_cookie(
        key=_SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=bool(getattr(request.app.state, "cloud_secure_cookies", True)),
        samesite="lax",
    )
    return response
