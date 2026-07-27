"""Health and local passphrase routes."""

from fastapi import APIRouter, Request

from api.schemas import (
    AuthRequest,
    AuthResponse,
)

health_router = APIRouter()
router = APIRouter()


@health_router.get("/health")
async def health() -> dict[str, str]:
    """Health check for local and deployment probes."""
    return {"status": "ok"}


@router.post("/api/v1/auth")
async def verify_passphrase(request: Request, payload: AuthRequest) -> AuthResponse:
    """Verify a passphrase and return the scoped user ID."""
    auth = getattr(request.app.state, "auth", None)
    if auth is None:
        return AuthResponse(user_id="anonymous", valid=True)

    user_id = auth.verify(payload.passphrase)
    if user_id is None:
        return AuthResponse(user_id="", valid=False)
    return AuthResponse(user_id=user_id, valid=True)
