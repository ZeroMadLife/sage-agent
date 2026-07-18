"""Static release contracts for the private Canary deployment."""

from pathlib import Path

from api.main import create_app
from core.config.settings import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_private_canary_exposes_only_the_loopback_gateway() -> None:
    compose = (ROOT / "infra/compose/private-canary.yml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${SAGE_GATEWAY_PORT:-8080}:8080"' in compose
    assert '"5432:5432"' not in compose
    assert '"6379:6379"' not in compose
    assert "SAGE_CODING_SANDBOX_PROVIDER: container" in compose
    assert "APP_ENV: production" in compose


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
    assert "UNIX-CONNECT:/run/user/1002/docker.sock" in proxy


def test_api_image_uses_the_official_retrying_package_index() -> None:
    dockerfile = (ROOT / "infra/docker/sage-api.Dockerfile").read_text(encoding="utf-8")

    assert "PIP_INDEX_URL=https://pypi.org/simple" in dockerfile
    assert "PIP_RETRIES=10" in dockerfile
    assert "pip install --no-cache-dir --upgrade pip" not in dockerfile
    assert "apt-get install --no-install-recommends -y git" in dockerfile


def test_web_image_cannot_disable_the_production_login_gate() -> None:
    dockerfile = (ROOT / "infra/docker/sage-web.Dockerfile").read_text(encoding="utf-8")
    compose = (ROOT / "infra/compose/private-canary.yml").read_text(encoding="utf-8")

    assert "RUN VITE_CLOUD_AUTH_REQUIRED=true npm run build" in dockerfile
    assert "ARG VITE_CLOUD_AUTH_REQUIRED" not in dockerfile
    assert "VITE_CLOUD_AUTH_REQUIRED:" not in compose


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
