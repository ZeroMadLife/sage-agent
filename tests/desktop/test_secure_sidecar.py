"""Security contracts shared by the D1 desktop host and sidecar."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from desktop.sidecar.app import DESKTOP_API_VERSION, create_desktop_app
from desktop.sidecar.security import DesktopBootstrap, DesktopSecurity

BEARER = "test-only-bearer"
ORIGIN = "tauri://localhost"
HOST = "127.0.0.1:43123"


def _security() -> DesktopSecurity:
    return DesktopSecurity(bearer=BEARER, origin=ORIGIN, host=HOST)


def _headers(**overrides: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {BEARER}",
        "Origin": ORIGIN,
        "Host": HOST,
    }
    headers.update(overrides)
    return headers


@contextmanager
def _running_secure_sidecar(tmp_path: Path) -> Iterator[subprocess.Popen[str]]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path.cwd()), str(Path.cwd() / "packages" / "sage_harness")]
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "desktop.sidecar", "--desktop-host"],
        cwd=Path.cwd(),
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdin is not None
        process.stdin.write(
            json.dumps(
                {
                    "instance_id": "test-instance",
                    "nonce": "test-nonce",
                    "bearer": BEARER,
                    "origin": ORIGIN,
                    "data_dir": str(tmp_path),
                }
            )
            + "\n"
        )
        process.stdin.flush()
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)


def test_bootstrap_rejects_missing_or_unknown_fields() -> None:
    valid = {
        "instance_id": "instance",
        "nonce": "nonce",
        "bearer": "bearer",
        "origin": ORIGIN,
        "data_dir": "/tmp/sage",
    }

    for field in valid:
        payload = dict(valid)
        del payload[field]
        with pytest.raises(ValueError, match="invalid desktop bootstrap"):
            DesktopBootstrap.from_json(json.dumps(payload))

    with pytest.raises(ValueError, match="invalid desktop bootstrap"):
        DesktopBootstrap.from_json(json.dumps({**valid, "secret": "must-fail"}))


@pytest.mark.parametrize(
    ("headers", "reason_code"),
    [
        ({"Authorization": "Bearer wrong"}, "desktop_bearer_rejected"),
        ({"Host": "localhost:43123"}, "desktop_host_rejected"),
        ({"Origin": "https://evil.example"}, "desktop_origin_rejected"),
    ],
)
def test_http_and_sse_fail_closed_for_each_security_dimension(
    tmp_path: Path,
    headers: dict[str, str],
    reason_code: str,
) -> None:
    app = create_desktop_app(
        data_dir=tmp_path,
        build_sha="test-build",
        security=_security(),
    )
    request_headers = _headers(**headers)

    with TestClient(app) as client:
        for path in ("/health/live", "/desktop/probe/sse"):
            response = client.get(path, headers=request_headers)
            assert response.status_code == 403
            assert response.json() == {"reason_code": reason_code, "action": "restart_sage"}
            assert BEARER not in response.text


def test_secure_http_sse_and_websocket_accept_the_same_session(tmp_path: Path) -> None:
    app = create_desktop_app(
        data_dir=tmp_path,
        build_sha="test-build",
        security=_security(),
    )

    with TestClient(app) as client:
        live = client.get("/health/live", headers=_headers())
        capabilities = client.get("/capabilities", headers=_headers())
        sse = client.get("/desktop/probe/sse", headers=_headers())
        with client.websocket_connect(
            "/desktop/probe/ws",
            headers={"Origin": ORIGIN, "Host": HOST},
            subprotocols=["sage.v1", f"sage-bearer.{BEARER}"],
        ) as websocket:
            ws_payload = websocket.receive_json()

    assert live.status_code == 200
    assert capabilities.status_code == 200
    assert capabilities.json()["status"] == "degraded"
    assert capabilities.json()["capabilities"]["provider"] == {
        "status": "blocked",
        "reason_code": "provider_not_configured",
        "action": "configure_provider",
    }
    assert sse.headers["content-type"].startswith("text/event-stream")
    assert "event: ready" in sse.text
    assert ws_payload == {"status": "ready", "api_version": DESKTOP_API_VERSION}


@pytest.mark.parametrize(
    ("headers", "subprotocols"),
    [
        ({"Origin": "https://evil.example", "Host": HOST}, ["sage.v1", f"sage-bearer.{BEARER}"]),
        ({"Origin": ORIGIN, "Host": "localhost:43123"}, ["sage.v1", f"sage-bearer.{BEARER}"]),
        ({"Origin": ORIGIN, "Host": HOST}, ["sage.v1", "sage-bearer.wrong"]),
    ],
)
def test_websocket_rejects_wrong_origin_host_or_bearer(
    tmp_path: Path,
    headers: dict[str, str],
    subprotocols: list[str],
) -> None:
    app = create_desktop_app(
        data_dir=tmp_path,
        build_sha="test-build",
        security=_security(),
    )

    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect(
            "/desktop/probe/ws",
            headers=headers,
            subprotocols=subprotocols,
        ),
    ):
        pass


def test_secure_process_reads_bootstrap_from_pipe_and_echoes_only_public_handshake(
    tmp_path: Path,
) -> None:
    with _running_secure_sidecar(tmp_path) as process:
        assert process.stdout is not None
        handshake = json.loads(process.stdout.readline())

    assert handshake == {
        "pid": process.pid,
        "port": handshake["port"],
        "instance_id": "test-instance",
        "api_version": DESKTOP_API_VERSION,
        "build_sha": "dev",
        "nonce": "test-nonce",
    }
    assert isinstance(handshake["port"], int) and handshake["port"] > 0
    assert BEARER not in json.dumps(handshake)


def test_secure_process_exits_when_the_parent_pipe_closes(tmp_path: Path) -> None:
    with _running_secure_sidecar(tmp_path) as process:
        assert process.stdout is not None
        json.loads(process.stdout.readline())
        assert process.stdin is not None
        process.stdin.close()
        process.stdin = None
        return_code = process.wait(timeout=10)

    assert return_code == 0
