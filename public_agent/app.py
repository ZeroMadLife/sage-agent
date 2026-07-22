"""Standalone FastAPI process for the restricted Sage public Agent."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from public_agent.corpus import PublicPackage
from public_agent.limiter import SlidingWindowRateLimiter
from public_agent.model import OpenAIPublicAnswerModel, PublicAnswerModel
from public_agent.schemas import PublicAskRequest, PublicAskResponse, PublicHealthResponse
from public_agent.service import PublicAgentService

DEFAULT_PACKAGE = Path(__file__).resolve().parent.parent / "data" / "public" / "sage-public-v1.json"


def create_public_agent_app(
    *,
    package_path: str | Path | None = None,
    model: PublicAnswerModel | None = None,
    limiter: SlidingWindowRateLimiter | None = None,
) -> FastAPI:
    resolved_package_path = package_path or os.getenv("SAGE_PUBLIC_PACKAGE") or DEFAULT_PACKAGE
    package = PublicPackage.load(resolved_package_path)
    resolved_model = model if model is not None else _model_from_env()
    service = PublicAgentService(package, resolved_model)
    rate_limiter = limiter or SlidingWindowRateLimiter(
        requests=int(os.getenv("SAGE_PUBLIC_RATE_LIMIT_REQUESTS", "12")),
        window_seconds=int(os.getenv("SAGE_PUBLIC_RATE_LIMIT_WINDOW_SECONDS", "60")),
    )
    app = FastAPI(title="Sage Public Agent", docs_url=None, redoc_url=None)
    app.state.public_agent_service = service
    app.state.public_agent_limiter = rate_limiter

    @app.middleware("http")
    async def public_security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Public-Package-Revision"] = package.revision
        return response

    @app.get("/healthz", response_model=PublicHealthResponse)
    async def health() -> PublicHealthResponse:
        return PublicHealthResponse(
            status="ready" if service.ready else "not_configured",
            package_id=package.package_id,
            package_revision=package.revision,
            package_digest=package.digest,
        )

    @app.post("/api/public/v1/ask", response_model=PublicAskResponse)
    async def ask(
        payload: PublicAskRequest, request: Request, response: Response
    ) -> PublicAskResponse | Response:
        client_key = request.client.host if request.client is not None else "unknown"
        decision = rate_limiter.check(client_key)
        response.headers["X-RateLimit-Limit"] = str(rate_limiter.requests)
        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        response.headers["X-Public-Package-Revision"] = package.revision
        if not decision.allowed:
            return JSONResponse(
                status_code=429,
                headers={
                    "Cache-Control": "no-store",
                    "Retry-After": str(decision.retry_after_seconds),
                    "X-RateLimit-Limit": str(rate_limiter.requests),
                    "X-RateLimit-Remaining": "0",
                    "X-Public-Package-Revision": package.revision,
                },
                content={"detail": "public Agent rate limit exceeded"},
            )
        if not service.ready:
            raise HTTPException(
                status_code=503,
                detail="public Agent is not configured",
                headers={
                    "X-RateLimit-Limit": str(rate_limiter.requests),
                    "X-RateLimit-Remaining": str(decision.remaining),
                },
            )
        try:
            result = await service.answer(payload.question)
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="public Agent is temporarily unavailable"
            ) from exc
        body = asdict(result)
        body["usage"] = {
            "input_tokens": body.pop("input_tokens"),
            "output_tokens": body.pop("output_tokens"),
        }
        return PublicAskResponse.model_validate(body)

    return app


def _model_from_env() -> PublicAnswerModel | None:
    api_key = os.getenv("SAGE_PUBLIC_AGENT_API_KEY", "").strip()
    base_url = os.getenv("SAGE_PUBLIC_AGENT_BASE_URL", "").strip()
    model = os.getenv("SAGE_PUBLIC_AGENT_MODEL", "").strip()
    if not all((api_key, base_url, model)):
        return None
    return OpenAIPublicAnswerModel(api_key=api_key, base_url=base_url, model=model)


app = create_public_agent_app()
