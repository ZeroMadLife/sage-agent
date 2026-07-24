"""Standalone FastAPI process for the restricted Sage public Agent."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from public_agent.budget import DailyTokenBudget, PublicBudgetExceeded
from public_agent.client_identity import (
    PublicClientIdentityError,
    PublicClientIdentityResolver,
)
from public_agent.corpus import PublicPackage
from public_agent.limiter import SlidingWindowRateLimiter
from public_agent.model import OpenAIPublicAnswerModel, PublicAnswerModel
from public_agent.registry import PublishedPackageError, PublishedPackageProvider
from public_agent.schemas import PublicAskRequest, PublicAskResponse, PublicHealthResponse
from public_agent.service import PublicAgentResult, PublicAgentService

DEFAULT_PACKAGE = Path(__file__).resolve().parent.parent / "data" / "public" / "sage-public-v1.json"


def create_public_agent_app(
    *,
    package_path: str | Path | None = None,
    package_registry_root: str | Path | None = None,
    model: PublicAnswerModel | None = None,
    limiter: SlidingWindowRateLimiter | None = None,
    client_identity: PublicClientIdentityResolver | None = None,
    token_budget: DailyTokenBudget | None = None,
) -> FastAPI:
    resolved_registry_root = package_registry_root or os.getenv("SAGE_PUBLIC_PACKAGE_REGISTRY")
    if resolved_registry_root:
        package_source: PublicPackage | PublishedPackageProvider = PublishedPackageProvider(
            resolved_registry_root
        )
    else:
        resolved_package_path = package_path or os.getenv("SAGE_PUBLIC_PACKAGE") or DEFAULT_PACKAGE
        package_source = PublicPackage.load(resolved_package_path)
    resolved_model = model if model is not None else _model_from_env()
    resolved_budget = token_budget or DailyTokenBudget(
        token_limit=_bounded_int("SAGE_PUBLIC_DAILY_TOKEN_LIMIT", 120_000, 1_000, 10_000_000),
        reservation_tokens=_bounded_int(
            "SAGE_PUBLIC_REQUEST_TOKEN_RESERVATION", 8_000, 500, 100_000
        ),
        state_path=os.getenv("SAGE_PUBLIC_BUDGET_STATE_PATH") or None,
    )
    service = PublicAgentService(package_source, resolved_model, token_budget=resolved_budget)
    rate_limiter = limiter or SlidingWindowRateLimiter(
        requests=int(os.getenv("SAGE_PUBLIC_RATE_LIMIT_REQUESTS", "12")),
        window_seconds=int(os.getenv("SAGE_PUBLIC_RATE_LIMIT_WINDOW_SECONDS", "60")),
    )
    app = FastAPI(title="Sage Public Agent", docs_url=None, redoc_url=None)
    app.state.public_agent_service = service
    app.state.public_agent_limiter = rate_limiter
    identity_resolver = client_identity or PublicClientIdentityResolver.from_cidrs(
        os.getenv("SAGE_PUBLIC_TRUSTED_PROXY_CIDRS", ""),
        header_name=os.getenv("SAGE_PUBLIC_TRUSTED_CLIENT_HEADER", "X-Sage-Public-Client-IP"),
    )

    @app.middleware("http")
    async def public_security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if "X-Public-Package-Revision" not in response.headers:
            try:
                response.headers["X-Public-Package-Revision"] = service.package.revision
            except PublishedPackageError:
                response.headers["X-Public-Package-Revision"] = "unavailable"
        return response

    @app.get("/healthz", response_model=PublicHealthResponse)
    async def health(response: Response) -> PublicHealthResponse:
        try:
            package = service.package
        except PublishedPackageError:
            response.headers["X-Public-Package-Revision"] = "unavailable"
            return PublicHealthResponse(
                status="degraded",
                package_id="unavailable",
                package_revision="unavailable",
                package_digest="unavailable",
            )
        response.headers["X-Public-Package-Revision"] = package.revision
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
        try:
            client_key = identity_resolver.resolve(request)
        except PublicClientIdentityError as exc:
            raise HTTPException(status_code=400, detail="invalid public proxy identity") from exc
        try:
            package = service.package
        except PublishedPackageError as exc:
            raise HTTPException(status_code=503, detail="public package unavailable") from exc
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
        if "text/event-stream" in request.headers.get("accept", "").casefold():
            return StreamingResponse(
                _stream_answer(service, payload.question, package),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-store",
                    "X-Accel-Buffering": "no",
                    "X-RateLimit-Limit": str(rate_limiter.requests),
                    "X-RateLimit-Remaining": str(decision.remaining),
                    "X-Public-Package-Revision": package.revision,
                },
            )
        try:
            result = await service.answer(payload.question, package=package)
        except PublicBudgetExceeded as exc:
            raise HTTPException(
                status_code=429,
                detail="public Agent usage budget exceeded",
                headers={"Retry-After": "3600"},
            ) from exc
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


async def _stream_answer(
    service: PublicAgentService,
    question: str,
    package: PublicPackage,
) -> AsyncIterator[str]:
    yield _sse("stage", {"stage": "retrieving", "label": "检索公开资料"})
    try:
        result = await service.answer(question, package=package)
    except PublicBudgetExceeded:
        yield _sse(
            "error",
            {"message": "公开问答当前已达使用上限，请稍后重试", "retry_after": 3600},
        )
        return
    except Exception:
        yield _sse("error", {"message": "公开问答服务暂时不可用"})
        return

    yield _sse("stage", {"stage": "grounding", "label": "核对回答依据"})
    for delta in _answer_deltas(result.answer):
        yield _sse("answer_delta", {"delta": delta})
        await asyncio.sleep(0)
    yield _sse(
        "sources",
        {"citations": [asdict(citation) for citation in result.citations]},
    )
    yield _sse("completed", _completed_payload(result))


def _answer_deltas(answer: str, *, chunk_chars: int = 24) -> tuple[str, ...]:
    return tuple(answer[index : index + chunk_chars] for index in range(0, len(answer), chunk_chars))


def _completed_payload(result: PublicAgentResult) -> dict[str, object]:
    return {
        "status": result.status,
        "receipt": asdict(result.receipt),
        "usage": {
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
    }


def _sse(event: str, data: dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _model_from_env() -> PublicAnswerModel | None:
    api_key = os.getenv("SAGE_PUBLIC_AGENT_API_KEY", "").strip()
    base_url = os.getenv("SAGE_PUBLIC_AGENT_BASE_URL", "").strip()
    model = os.getenv("SAGE_PUBLIC_AGENT_MODEL", "").strip()
    if not all((api_key, base_url, model)):
        return None
    return OpenAIPublicAnswerModel(
        api_key=api_key,
        base_url=base_url,
        model=model,
        timeout_seconds=_bounded_float("SAGE_PUBLIC_AGENT_TIMEOUT_SECONDS", 15.0, 2.0, 60.0),
        max_output_tokens=_bounded_int("SAGE_PUBLIC_AGENT_MAX_OUTPUT_TOKENS", 500, 64, 2_000),
    )


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


app = create_public_agent_app()
