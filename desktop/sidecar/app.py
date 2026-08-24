"""Minimal FastAPI application for the D0 desktop sidecar."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from desktop.sidecar.smoke import ProbeCheck, StartupSmoke, run_startup_smoke

DESKTOP_PROFILE = "desktop-minimal"
DESKTOP_API_VERSION = "1"
DESKTOP_HEALTH_SCHEMA_VERSION = "1"
SmokeRunner = Callable[[Path], Awaitable[StartupSmoke]]


def create_desktop_app(
    *,
    data_dir: Path,
    build_sha: str,
    smoke_runner: SmokeRunner = run_startup_smoke,
) -> FastAPI:
    """Create the local-only profile without loading the full product API."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.desktop_smoke = await smoke_runner(data_dir)
        yield

    app = FastAPI(title="Sage Desktop Sidecar", lifespan=lifespan)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {
            "status": "live",
            "profile": DESKTOP_PROFILE,
            "api_version": DESKTOP_API_VERSION,
            "build_sha": build_sha,
        }

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        smoke: StartupSmoke = app.state.desktop_smoke
        build_check = (
            ProbeCheck(status="ready", version=build_sha)
            if build_sha and build_sha != "unknown"
            else ProbeCheck(
                status="blocked",
                version="unknown",
                reason_code="build_identity_missing",
            )
        )
        checks = {
            "api": ProbeCheck(status="ready", version=DESKTOP_API_VERSION),
            "build": build_check,
            "schema": ProbeCheck(status="ready", version=DESKTOP_HEALTH_SCHEMA_VERSION),
            **smoke.checks,
        }
        status = "ready" if all(check.status == "ready" for check in checks.values()) else "blocked"
        payload = {
            "status": status,
            "profile": DESKTOP_PROFILE,
            "api_version": DESKTOP_API_VERSION,
            "build_sha": build_sha,
            "checks": {name: check.as_dict() for name, check in checks.items()},
        }
        return JSONResponse(payload, status_code=200 if status == "ready" else 503)

    return app


__all__ = [
    "DESKTOP_API_VERSION",
    "DESKTOP_HEALTH_SCHEMA_VERSION",
    "DESKTOP_PROFILE",
    "create_desktop_app",
]
