"""Static release contracts for the private Canary deployment."""

# ruff: noqa: I001 - Ruff 0.8 and 0.15 disagree on the local package import section.

import subprocess
from pathlib import Path

from api.main import create_app
from core.config.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_private_canary_exposes_only_the_loopback_gateway() -> None:
    compose = (ROOT / "infra/compose/private-canary.yml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${SAGE_GATEWAY_PORT:-8080}:8080"' in compose
    assert '"127.0.0.1:${SAGE_PUBLIC_PORT:-8081}:8081"' in compose
    assert '"5432:5432"' not in compose
    assert '"6379:6379"' not in compose
    assert "SAGE_CODING_SANDBOX_PROVIDER: container" in compose
    assert "APP_ENV: production" in compose


def test_public_profile_is_built_as_an_api_isolated_static_surface() -> None:
    compose = (ROOT / "infra/compose/private-canary.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "infra/docker/sage-public.Dockerfile").read_text(encoding="utf-8")
    caddyfile = (ROOT / "infra/proxy/Caddyfile.public").read_text(encoding="utf-8")
    router = (ROOT / "frontend/src/router/public.ts").read_text(encoding="utf-8")

    public_service = compose[compose.index("  public:") : compose.index("\nnetworks:")]
    assert "infra/docker/sage-public.Dockerfile" in public_service
    assert "env_file:" not in public_service
    assert "depends_on:" not in public_service
    assert 'user: "65532:65532"' in public_service
    assert "read_only: true" in public_service
    assert "npm run build:public" in dockerfile
    assert "dist-public" in dockerfile
    assert "reverse_proxy" not in caddyfile
    assert "connect-src 'none'" in caddyfile
    assert "frame-ancestors 'none'" in caddyfile
    assert "PublicProfileView" in router
    assert "AssistantHomeView" not in router
    assert "KnowledgeView" not in router
    assert "CodingView" not in router


def test_private_canary_environment_template_tracks_server_topology() -> None:
    template = (ROOT / "infra/env/private-canary.env.example").read_text(
        encoding="utf-8"
    )

    assert "SAGE_DOCKER_REGISTRY=docker.m.daocloud.io" in template
    assert "SAGE_ROOTLESS_DOCKER_SOCKET=/run/user/1002/sage-sandbox.sock" in template
    assert "SAGE_SANDBOX_DOCKER_SOCKET=/run/user/1003/docker.sock" in template
    assert "SAGE_SANDBOX_UID=1003" in template
    assert (
        "SAGE_CODING_SANDBOX_IMAGE=docker.m.daocloud.io/library/python:3.12-slim"
        in template
    )
    assert "CLOUD_CANARY_INVITE_LOGIN_ENABLED=true" in template


def test_private_canary_requires_a_rootless_sandbox_socket() -> None:
    compose = (ROOT / "infra/compose/private-canary.yml").read_text(encoding="utf-8")

    assert "SAGE_ROOTLESS_DOCKER_SOCKET:?rootless Docker socket is required" in compose
    assert "/var/run/docker.sock" not in compose
    assert "/opt/sage/data/workspaces:/opt/sage/data/workspaces" in compose
    assert "no-new-privileges:true" in compose

    proxy = (ROOT / "infra/systemd/sage-sandbox-proxy.service").read_text(
        encoding="utf-8"
    )
    assert "mode=0600,user=sage-deploy,group=sage-deploy" in proxy
    assert "UNIX-CONNECT:/run/user/1003/docker.sock" in proxy
    assert "ProtectHome=false" in proxy
    assert "InaccessiblePaths=/home /root" in proxy

    delegation = (ROOT / "infra/systemd/sage-rootless-user-delegation.conf").read_text(
        encoding="utf-8"
    )
    assert "Delegate=cpu cpuset io memory pids" in delegation


def test_api_image_uses_the_configurable_canary_package_index() -> None:
    dockerfile = (ROOT / "infra/docker/sage-api.Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "infra/compose/private-canary.yml").read_text(encoding="utf-8")

    assert "ARG SAGE_PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/" in dockerfile
    assert "PIP_INDEX_URL=${SAGE_PIP_INDEX_URL}" in dockerfile
    assert "SAGE_PIP_INDEX_URL: ${SAGE_PIP_INDEX_URL:-" in compose
    assert "PIP_RETRIES=10" in dockerfile
    assert "--mount=type=cache,target=/root/.cache/pip" in dockerfile
    assert "pip install -r requirements.txt" in dockerfile
    assert "pip install --no-cache-dir --upgrade pip" not in dockerfile
    assert "Acquire::ForceIPv4=true install --no-install-recommends -y git" in dockerfile
    assert "ARG SAGE_DOCKER_REGISTRY=docker.io" in dockerfile
    assert "${SAGE_DOCKER_REGISTRY}/library/python:3.12.13-slim-bookworm" in dockerfile


def test_api_image_uses_configurable_debian_mirrors_and_ipv4() -> None:
    dockerfile = (ROOT / "infra/docker/sage-api.Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "infra/compose/private-canary.yml").read_text(encoding="utf-8")

    assert "ARG SAGE_DEBIAN_MIRROR=https://mirrors.aliyun.com/debian" in dockerfile
    assert (
        "ARG SAGE_DEBIAN_SECURITY_MIRROR=https://mirrors.aliyun.com/debian-security"
        in dockerfile
    )
    assert "http://deb.debian.org/debian-security" in dockerfile
    assert "Acquire::ForceIPv4=true update" in dockerfile
    assert "Acquire::ForceIPv4=true install" in dockerfile
    assert "SAGE_DEBIAN_MIRROR: ${SAGE_DEBIAN_MIRROR:-" in compose
    assert "SAGE_DEBIAN_SECURITY_MIRROR: ${SAGE_DEBIAN_SECURITY_MIRROR:-" in compose


def test_api_entrypoint_runs_explicit_migration_commands() -> None:
    entrypoint = ROOT / "infra/docker/sage-api-entrypoint.sh"

    completed = subprocess.run(
        ["sh", str(entrypoint), "printf", "%s", "migration-command"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout == "migration-command"


def test_web_image_cannot_disable_the_production_login_gate() -> None:
    dockerfile = (ROOT / "infra/docker/sage-web.Dockerfile").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "infra/compose/private-canary.yml").read_text(encoding="utf-8")

    assert "RUN VITE_CLOUD_AUTH_REQUIRED=true npm run build" in dockerfile
    assert "setcap -r /usr/bin/caddy" in dockerfile
    assert 'test -z "$(getcap /usr/bin/caddy)"' in dockerfile
    assert "ARG VITE_CLOUD_AUTH_REQUIRED" not in dockerfile
    assert "VITE_CLOUD_AUTH_REQUIRED:" not in compose
    assert "SAGE_DOCKER_REGISTRY: ${SAGE_DOCKER_REGISTRY:-docker.io}" in compose


def test_private_proxy_routes_backend_before_the_spa_fallback() -> None:
    caddyfile = (ROOT / "infra/proxy/Caddyfile.private").read_text(encoding="utf-8")
    dockerfile = (ROOT / "infra/docker/sage-web.Dockerfile").read_text(encoding="utf-8")

    assert "handle /api/*" in caddyfile
    assert "handle /health" in caddyfile
    assert caddyfile.index("handle /health") < caddyfile.index("try_files")
    assert "@backend" not in caddyfile
    assert "caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile" in dockerfile


def test_deployctl_uses_python_310_compatible_utc() -> None:
    source = (ROOT / "scripts/deployctl.py").read_text(encoding="utf-8")

    assert "from datetime import UTC" not in source
    assert "_UTC = timezone.utc" in source
    assert "datetime.now(_UTC)" in source


def test_deployctl_allows_slow_first_time_dependency_builds() -> None:
    source = (ROOT / "scripts/deployctl.py").read_text(encoding="utf-8")

    assert "BUILD_TIMEOUT_SECONDS = 60 * 60" in source
    assert "timeout=BUILD_TIMEOUT_SECONDS" in source


def test_production_paths_can_live_on_persistent_volumes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspaces"
    storage = tmp_path / "coding"
    settings = Settings(
        sage_coding_workspace_root=str(workspace),
        sage_coding_storage_root=str(storage),
    )
    monkeypatch.setattr("api.main.get_settings", lambda: settings)

    app = create_app()

    assert app.state.coding_workspace_root == workspace
    assert app.state.coding_storage_root == storage
