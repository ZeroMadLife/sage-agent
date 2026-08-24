"""D2 local product profile contracts for the desktop sidecar."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from desktop.sidecar.app import create_desktop_app, derive_capability_status
from desktop.sidecar.security import (
    DesktopProviderBootstrap,
    DesktopRuntimeBootstrap,
    DesktopSecurity,
)

BEARER = "test-only-bearer"
ORIGIN = "tauri://localhost"
HOST = "127.0.0.1:43123"


class FakeModel:
    pass


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {BEARER}",
        "Origin": ORIGIN,
        "Host": HOST,
    }


def test_local_product_profile_exposes_conversation_and_sqlite_rag_without_side_effects(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = "test-secret-product-runtime"
    runtime = DesktopRuntimeBootstrap(
        workspace_path=workspace,
        provider=DesktopProviderBootstrap(
            provider_id="provider-1",
            base_url="https://provider.example/v1",
            default_model="model-small",
            api_mode="openai_chat_completions",
            api_key=secret,
        ),
    )
    app = create_desktop_app(
        data_dir=tmp_path / "data",
        build_sha="test-build",
        security=DesktopSecurity(bearer=BEARER, origin=ORIGIN, host=HOST),
        runtime=runtime,
        model_factory=lambda *_args, **_kwargs: FakeModel(),
    )

    with TestClient(app) as client:
        capabilities = client.get("/capabilities", headers=_headers())
        assistant = client.get("/api/v1/assistant/home", headers=_headers())
        knowledge = client.get("/api/v1/knowledge", headers=_headers())
        session = client.post("/api/v1/coding/session", headers=_headers(), json={})
        catalog = client.get(
            "/api/v1/harness/capabilities",
            headers=_headers(),
            params={
                "session_id": session.json()["session_id"],
                "surface": "coding",
                "origin": "local",
            },
        )
        cloud_oauth = client.get("/api/v1/cloud/auth/options", headers=_headers())

    assert capabilities.json()["capabilities"]["conversation"]["status"] == "ready"
    assert capabilities.json()["capabilities"]["rag"]["status"] == "ready"
    assert capabilities.json()["capabilities"]["side_effect_tools"]["status"] == "blocked"
    assert assistant.status_code == 200
    assert assistant.json()["identity"]["mode"] == "local"
    assert knowledge.status_code == 200
    assert session.status_code == 200
    capability_ids = {item["capability_id"] for item in catalog.json()["capabilities"]}
    assert {"local:list_files", "local:read_file", "local:search"}.issubset(capability_ids)
    assert {"local:write_file", "local:patch_file", "local:run_shell"}.isdisjoint(capability_ids)
    assert cloud_oauth.status_code == 404
    assert all(value != secret for value in os.environ.values())
    assert secret.encode() not in (tmp_path / "data" / "knowledge.sqlite3").read_bytes()


def test_capability_status_derives_critical_and_optional_matrix() -> None:
    ready = {
        name: {"status": "ready"}
        for name in (
            "api",
            "storage",
            "checkpoint",
            "provider",
            "conversation",
            "rag",
            "side_effect_tools",
        )
    }
    assert derive_capability_status(ready) == "ready"

    optional_blocked = {**ready, "side_effect_tools": {"status": "blocked"}}
    assert derive_capability_status(optional_blocked) == "degraded"

    critical_degraded = {**ready, "rag": {"status": "degraded"}}
    assert derive_capability_status(critical_degraded) == "degraded"

    for critical in ("api", "storage", "checkpoint", "provider", "conversation", "rag"):
        blocked = {**ready, critical: {"status": "blocked"}}
        assert derive_capability_status(blocked) == "blocked"


def test_product_capability_status_is_blocked_without_runtime_and_degraded_without_docker(
    tmp_path: Path,
) -> None:
    minimal = create_desktop_app(data_dir=tmp_path / "minimal", build_sha="test-build")
    with TestClient(minimal) as client:
        assert client.get("/capabilities").json()["status"] == "blocked"

    workspace = tmp_path / "workspace-status"
    workspace.mkdir()
    runtime = DesktopRuntimeBootstrap(
        workspace_path=workspace,
        provider=DesktopProviderBootstrap(
            provider_id="provider-status",
            base_url="https://provider.example/v1",
            default_model="model-small",
            api_mode="openai_chat_completions",
            api_key="test-secret-status",
        ),
    )
    product = create_desktop_app(
        data_dir=tmp_path / "product",
        build_sha="test-build",
        runtime=runtime,
        model_factory=lambda *_args, **_kwargs: FakeModel(),
    )
    with TestClient(product) as client:
        assert client.get("/capabilities").json()["status"] == "degraded"
