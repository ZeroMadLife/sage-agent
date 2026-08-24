"""Minimal FastAPI application for the D0 desktop sidecar."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse

from desktop.sidecar.security import DesktopSecurity, DesktopSecurityMiddleware
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
    security: DesktopSecurity | None = None,
) -> FastAPI:
    """Create the local-only profile without loading the full product API."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.desktop_smoke = await smoke_runner(data_dir)
        yield

    app = FastAPI(title="Sage Desktop Sidecar", lifespan=lifespan)
    capability_observed = False
    capability_observation_lock = threading.Lock()
    if security is not None:
        app.add_middleware(DesktopSecurityMiddleware, security=security)

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

    @app.get("/capabilities")
    async def capabilities() -> dict[str, object]:
        nonlocal capability_observed
        if security is not None:
            with capability_observation_lock:
                if not capability_observed:
                    capability_observed = _record_capability_observation(data_dir)
        return {
            "status": "degraded",
            "api_version": DESKTOP_API_VERSION,
            "build_sha": build_sha,
            "capabilities": {
                "api": {"status": "ready", "reason_code": None, "action": None},
                "storage": {"status": "ready", "reason_code": None, "action": None},
                "checkpoint": {"status": "ready", "reason_code": None, "action": None},
                "provider": {
                    "status": "blocked",
                    "reason_code": "provider_not_configured",
                    "action": "configure_provider",
                },
                "knowledge": {
                    "status": "degraded",
                    "reason_code": "knowledge_profile_minimal",
                    "action": "continue_without_knowledge",
                },
                "sandbox": {
                    "status": "blocked",
                    "reason_code": "sandbox_not_available",
                    "action": "continue_without_tools",
                },
            },
        }

    @app.get("/desktop/probe/sse")
    async def sse_probe() -> StreamingResponse:
        async def event() -> AsyncIterator[str]:
            yield f"event: ready\ndata: {{\"api_version\":\"{DESKTOP_API_VERSION}\"}}\n\n"

        return StreamingResponse(event(), media_type="text/event-stream")

    @app.websocket("/desktop/probe/ws")
    async def websocket_probe(websocket: WebSocket) -> None:
        if security is None:
            await websocket.close(code=1008, reason="desktop_security_required")
            return
        protocols = [
            protocol.strip()
            for protocol in websocket.headers.get("sec-websocket-protocol", "").split(",")
            if protocol.strip()
        ]
        reason = security.websocket_reject_reason(
            host=websocket.headers.get("host"),
            origin=websocket.headers.get("origin"),
            subprotocols=protocols,
        )
        if reason is not None:
            await websocket.close(code=1008, reason=reason)
            return
        await websocket.accept(subprotocol="sage.v1")
        await websocket.send_json({"status": "ready", "api_version": DESKTOP_API_VERSION})
        await websocket.close(code=1000)

    return app


def _record_capability_observation(data_dir: Path) -> bool:
    """Record one credential-free proof that this process served the WebView."""
    record = {
        "timestamp": int(time.time()),
        "event": "webview_capabilities_observed",
        "state": "ready",
        "reason_code": "desktop_session_authenticated",
    }
    try:
        diagnostics = data_dir / "diagnostics"
        diagnostics.mkdir(parents=True, exist_ok=True)
        path = diagnostics / "desktop-sidecar.jsonl"
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return True
    except OSError:
        return False


__all__ = [
    "DESKTOP_API_VERSION",
    "DESKTOP_HEALTH_SCHEMA_VERSION",
    "DESKTOP_PROFILE",
    "create_desktop_app",
]
