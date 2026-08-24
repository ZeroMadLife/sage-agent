"""Public contracts for the minimal desktop sidecar."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.request import urlopen

import pytest
from fastapi.testclient import TestClient

from desktop.sidecar.app import DESKTOP_API_VERSION, create_desktop_app
from desktop.sidecar.smoke import ProbeCheck, StartupSmoke


@contextmanager
def _running_source_sidecar(tmp_path: Path, *extra_args: str) -> Iterator[subprocess.Popen[str]]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path.cwd()), str(Path.cwd() / "packages" / "sage_harness")]
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "desktop.sidecar",
            "--data-dir",
            str(tmp_path),
            *extra_args,
        ],
        cwd=Path.cwd(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)


def test_desktop_profile_reports_live_and_ready_without_private_paths(tmp_path: Path) -> None:
    app = create_desktop_app(data_dir=tmp_path, build_sha="test-build")

    with TestClient(app) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")

    assert live.status_code == 200
    assert live.json() == {
        "status": "live",
        "profile": "desktop-minimal",
        "api_version": DESKTOP_API_VERSION,
        "build_sha": "test-build",
    }
    assert ready.status_code == 200
    payload = ready.json()
    assert payload["status"] == "ready"
    assert payload["profile"] == "desktop-minimal"
    assert payload["api_version"] == DESKTOP_API_VERSION
    assert payload["build_sha"] == "test-build"
    assert set(payload["checks"]) == {
        "api",
        "build",
        "schema",
        "storage",
        "checkpoint",
        "tls",
        "core_imports",
    }
    assert {check["status"] for check in payload["checks"].values()} == {"ready"}
    serialized = json.dumps(payload, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "provider" not in serialized.lower()
    assert (tmp_path / "sage.sqlite3").is_file()
    assert (tmp_path / "checkpoints.sqlite3").is_file()


def test_readiness_is_blocked_with_a_public_reason_code(tmp_path: Path) -> None:
    async def blocked(_: Path) -> StartupSmoke:
        return StartupSmoke(
            checks={
                "storage": ProbeCheck(
                    status="blocked",
                    version="unknown",
                    reason_code="storage_probe_failed",
                )
            }
        )

    app = create_desktop_app(
        data_dir=tmp_path,
        build_sha="test-build",
        smoke_runner=blocked,
    )

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "blocked"
    assert response.json()["checks"]["storage"] == {
        "status": "blocked",
        "version": "unknown",
        "reason_code": "storage_probe_failed",
    }


def test_readiness_fails_closed_without_a_build_identity(tmp_path: Path) -> None:
    app = create_desktop_app(data_dir=tmp_path, build_sha="unknown")

    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["build"] == {
        "status": "blocked",
        "version": "unknown",
        "reason_code": "build_identity_missing",
    }


def test_source_sidecar_binds_one_os_assigned_loopback_port(tmp_path: Path) -> None:
    with _running_source_sidecar(
        tmp_path,
        "--bind",
        "127.0.0.1",
        "--port",
        "0",
        "--build-sha",
        "test-build",
    ) as process:
        assert process.stdout is not None
        startup = json.loads(process.stdout.readline())
        assert startup == {
            "event": "sidecar_started",
            "pid": process.pid,
            "bind": "127.0.0.1",
            "port": startup["port"],
            "profile": "desktop-minimal",
            "api_version": DESKTOP_API_VERSION,
            "build_sha": "test-build",
        }
        assert isinstance(startup["port"], int)
        assert startup["port"] > 0

        deadline = time.monotonic() + 10
        while True:
            try:
                with urlopen(
                    f"http://127.0.0.1:{startup['port']}/health/ready", timeout=1
                ) as response:
                    ready = json.load(response)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        assert ready["status"] == "ready"
        process.terminate()
        _, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--bind", "0.0.0.0"), "--bind must be 127.0.0.1"),
        (("--port", "8000"), "--port must be 0"),
    ],
)
def test_sidecar_rejects_non_random_or_non_loopback_bind(
    tmp_path: Path, args: tuple[str, str], message: str
) -> None:
    with _running_source_sidecar(tmp_path, *args) as process:
        _, stderr = process.communicate(timeout=10)

    assert process.returncode == 2
    assert message in stderr
