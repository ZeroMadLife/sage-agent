"""Minimal FastAPI application for the D0 desktop sidecar."""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse

from desktop.sidecar.security import (
    DesktopRuntimeBootstrap,
    DesktopSecurity,
    DesktopSecurityMiddleware,
)
from desktop.sidecar.smoke import ProbeCheck, StartupSmoke, run_startup_smoke

DESKTOP_PROFILE = "desktop-minimal"
DESKTOP_API_VERSION = "1"
DESKTOP_HEALTH_SCHEMA_VERSION = "1"
SmokeRunner = Callable[[Path], Awaitable[StartupSmoke]]
ModelFactory = Callable[..., Any]
_CRITICAL_DESKTOP_CAPABILITIES = frozenset(
    {"api", "storage", "checkpoint", "provider", "conversation", "rag"}
)


def derive_capability_status(capabilities: dict[str, dict[str, object]]) -> str:
    """Derive one public state without allowing optional services to block local use."""
    if any(
        capabilities.get(name, {}).get("status") == "blocked"
        for name in _CRITICAL_DESKTOP_CAPABILITIES
    ) or not _CRITICAL_DESKTOP_CAPABILITIES.issubset(capabilities):
        return "blocked"
    if any(value.get("status") != "ready" for value in capabilities.values()):
        return "degraded"
    return "ready"


def create_desktop_app(
    *,
    data_dir: Path,
    build_sha: str,
    smoke_runner: SmokeRunner = run_startup_smoke,
    security: DesktopSecurity | None = None,
    runtime: DesktopRuntimeBootstrap | None = None,
    model_factory: ModelFactory | None = None,
) -> FastAPI:
    """Create the secure diagnostic or local product desktop profile."""

    product_app = (
        _create_local_product_app(data_dir, runtime, model_factory=model_factory)
        if runtime is not None
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.desktop_smoke = await smoke_runner(data_dir)
        if product_app is None:
            yield
            return
        async with product_app.router.lifespan_context(product_app):
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
        product_ready = product_app is not None
        side_effect_tools_ready = bool(runtime and runtime.side_effect_tools_enabled)
        values: dict[str, dict[str, object]] = {
            "api": {"status": "ready", "reason_code": None, "action": None},
            "storage": {"status": "ready", "reason_code": None, "action": None},
            "checkpoint": {"status": "ready", "reason_code": None, "action": None},
            "provider": {
                "status": "ready" if product_ready else "blocked",
                "reason_code": None if product_ready else "provider_not_configured",
                "action": None if product_ready else "configure_provider",
            },
            "conversation": {
                "status": "ready" if product_ready else "blocked",
                "reason_code": None if product_ready else "provider_not_configured",
                "action": None if product_ready else "configure_provider",
            },
            "rag": {
                "status": "ready" if product_ready else "blocked",
                "reason_code": None if product_ready else "workspace_not_configured",
                "action": None if product_ready else "select_workspace",
            },
            "side_effect_tools": {
                "status": "ready" if side_effect_tools_ready else "blocked",
                "reason_code": None if side_effect_tools_ready else "docker_not_available",
                "action": (
                    None if side_effect_tools_ready else "continue_without_side_effect_tools"
                ),
            },
        }
        return {
            "status": derive_capability_status(values),
            "api_version": DESKTOP_API_VERSION,
            "build_sha": build_sha,
            "capabilities": values,
        }

    @app.get("/desktop/probe/sse")
    async def sse_probe() -> StreamingResponse:
        async def event() -> AsyncIterator[str]:
            yield f'event: ready\ndata: {{"api_version":"{DESKTOP_API_VERSION}"}}\n\n'

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

    if product_app is not None:
        app.mount("/", product_app)

    return app


def _create_local_product_app(
    data_dir: Path,
    runtime: DesktopRuntimeBootstrap,
    *,
    model_factory: ModelFactory | None,
) -> FastAPI:
    from api.main import create_app
    from core.knowledge.index import LocalKnowledgeIndex
    from core.knowledge.store import KnowledgeSourceRoot
    from core.llm import create_llm

    provider = runtime.provider
    model_spec = f"desktop:{provider.default_model}"

    def build_model(
        requested_model: str = model_spec,
        *,
        reasoning_mode: str = "off",
    ) -> Any:
        return create_llm(
            requested_model,
            reasoning_mode=reasoning_mode,
            api_key=provider.api_key,
            base_url=provider.base_url,
            api_mode=provider.api_mode,
        )

    data_dir.mkdir(parents=True, exist_ok=True)
    return create_app(
        coding_model_factory=model_factory or build_model,
        coding_workspace_root=runtime.workspace_path,
        coding_storage_root=data_dir / "coding",
        coding_model_catalog=[
            {
                "id": model_spec,
                "label": provider.default_model,
                "provider": "desktop",
                "reasoning_modes": [],
            }
        ],
        coding_model_capabilities={},
        coding_default_model=model_spec,
        coding_deerflow_v2_enabled=False,
        coding_default_runtime_profile="legacy",
        coding_context_assembly_mode="off",
        coding_sandbox_provider=runtime.sandbox_provider,
        coding_side_effect_tools_enabled=runtime.side_effect_tools_enabled,
        coding_web_fetch_enabled=False,
        coding_web_search_enabled=False,
        database_auto_migrate=False,
        cloud_app_env="development",
        cloud_routes_enabled=False,
        knowledge_workspace_root=runtime.workspace_path,
        knowledge_database_path=data_dir / "knowledge.sqlite3",
        knowledge_source_roots={
            "desktop-workspace": KnowledgeSourceRoot(
                root_id="desktop-workspace",
                kind="markdown",
                label="Desktop Workspace",
                path=runtime.workspace_path,
            )
        },
        knowledge_index=LocalKnowledgeIndex(workspace_id="desktop-local"),
        knowledge_jobs_enabled=False,
    )


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
    "derive_capability_status",
]
