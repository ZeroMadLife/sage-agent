"""Build receipt and artifact hygiene contracts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path

import pytest

import desktop.sidecar.build as sidecar_build
from desktop.sidecar.build import (
    ArtifactHygieneError,
    BuildEnvironmentError,
    build_receipt,
    parse_lock_manifest,
    smoke_packaged_artifact,
    verify_artifact_hygiene,
    verify_environment_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


def _fake_sidecar(
    tmp_path: Path,
    *,
    live_mode: str,
    ready_mode: str = "ready",
    additive_fields: bool = False,
    spawn_orphan: bool = False,
) -> Path:
    executable = tmp_path / "fake-sidecar"
    orphan_pid_file = tmp_path / "orphan.pid"
    sidecar_pid_file = tmp_path / "sidecar.pid"
    child_code = textwrap.dedent(
        """\
        import os
        import signal
        import sys
        import time
        from pathlib import Path

        grandchild = os.fork()
        if grandchild == 0:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
            while True:
                time.sleep(1)
        while True:
            time.sleep(1)
        """
    )
    executable.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            f"""\
            import argparse
            import json
            import os
            import signal
            import subprocess
            import sys
            import threading
            import time
            from http.server import BaseHTTPRequestHandler, HTTPServer

            LIVE_MODE = {live_mode!r}
            READY_MODE = {ready_mode!r}
            ADDITIVE_FIELDS = {additive_fields!r}
            BUILD_SHA = "fake-sha"
            SPAWN_ORPHAN = {spawn_orphan!r}
            ORPHAN_PID_FILE = {str(orphan_pid_file)!r}
            SIDECAR_PID_FILE = {str(sidecar_pid_file)!r}
            CHILD_CODE = {child_code!r}
            parser = argparse.ArgumentParser()
            parser.add_argument("--bind")
            parser.add_argument("--port")
            parser.add_argument("--data-dir")
            args = parser.parse_args()
            os.makedirs(args.data_dir, exist_ok=True)
            open(SIDECAR_PID_FILE, "w", encoding="utf-8").write(str(os.getpid()))
            open(os.path.join(args.data_dir, "sage.sqlite3"), "wb").close()
            open(os.path.join(args.data_dir, "checkpoints.sqlite3"), "wb").close()
            child = (
                subprocess.Popen(
                    [sys.executable, "-c", CHILD_CODE, ORPHAN_PID_FILE],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if SPAWN_ORPHAN
                else None
            )
            if child is not None:
                deadline = time.monotonic() + 2
                while not os.path.exists(ORPHAN_PID_FILE):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("fake orphan did not start")
                    time.sleep(0.01)

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path == "/health/live":
                        if LIVE_MODE == "404":
                            self.send_error(404)
                            return
                        payload = (
                            {{"status": "ok"}}
                            if LIVE_MODE == "wrong-schema"
                            else {{
                                "status": "live",
                                "profile": "desktop-minimal",
                                "api_version": "1",
                                "build_sha": BUILD_SHA,
                            }}
                        )
                    elif self.path == "/health/ready":
                        payload = {{
                            "status": "ready",
                            "profile": "desktop-minimal",
                            "api_version": "1",
                            "build_sha": BUILD_SHA,
                            "checks": {{
                                name: {{"status": "ready", "version": "1"}}
                                for name in (
                                    "api", "build", "schema", "storage",
                                    "checkpoint", "tls", "core_imports"
                                )
                            }},
                        }}
                        if READY_MODE == "missing-check":
                            del payload["checks"]["tls"]
                        elif READY_MODE == "blocked-check":
                            payload["checks"]["checkpoint"]["status"] = "blocked"
                        elif READY_MODE == "wrong-version-type":
                            payload["checks"]["storage"]["version"] = 1
                    else:
                        self.send_error(404)
                        return
                    if ADDITIVE_FIELDS:
                        payload["diagnostic"] = {{"generation": 2}}
                        if "checks" in payload:
                            payload["version"] = "2"
                            payload["checks"]["api"]["diagnostic"] = "compatible"
                            payload["checks"]["future_probe"] = {{
                                "status": "blocked",
                                "version": "2",
                                "diagnostic": "not required by D0",
                            }}
                    body = json.dumps(payload).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

                def log_message(self, *_):
                    pass

            server = HTTPServer(("127.0.0.1", 0), Handler)
            def stop(*_):
                if child is not None:
                    child.terminate()
                    child.wait(timeout=2)
                threading.Thread(target=server.shutdown, daemon=True).start()
            signal.signal(signal.SIGTERM, stop)
            print(json.dumps({{
                "event": "sidecar_started",
                "pid": os.getpid(),
                "bind": "127.0.0.1",
                "port": server.server_port,
                "profile": "desktop-minimal",
                "api_version": "1",
                "build_sha": BUILD_SHA,
            }}), flush=True)
            server.serve_forever()
            server.server_close()
            """
        ),
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _pinned_requirements(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if "==" in line and not line.startswith("#"):
            name, version = line.split("==", 1)
            pins[name.split("[", 1)[0].lower()] = version
    return pins


def test_build_module_imports_without_runtime_dependencies() -> None:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "-c", "import desktop.sidecar.build"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_minimal_runtime_versions_match_the_sage_release() -> None:
    root_pins = _pinned_requirements(ROOT / "requirements.txt")
    desktop_pins = _pinned_requirements(ROOT / "desktop" / "sidecar" / "requirements-runtime.txt")
    lock_pins = _pinned_requirements(ROOT / "desktop" / "sidecar" / "requirements-lock.txt")
    harness = tomllib.loads(
        (ROOT / "packages" / "sage_harness" / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert desktop_pins == {
        name: root_pins[name]
        for name in (
            "aiosqlite",
            "cryptography",
            "fastapi",
            "httpx",
            "langchain",
            "langchain-core",
            "langgraph",
            "langgraph-checkpoint-sqlite",
            "orjson",
            "psycopg2-binary",
            "pydantic",
            "tenacity",
            "uvicorn",
        )
    }
    assert lock_pins.items() >= desktop_pins.items()
    assert lock_pins["pyinstaller"] == "6.16.0"
    assert lock_pins["pyinstaller-hooks-contrib"] == "2026.6"
    assert lock_pins["hatchling"] == "1.27.0"
    assert all(
        not line.startswith("-e ")
        for line in (ROOT / "desktop" / "sidecar" / "requirements-lock.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    assert harness["project"]["requires-python"] == ">=3.12"


@pytest.mark.parametrize(
    "requirement",
    [
        "-e ./packages/sage_harness\n",
        "sage-harness @ file:///tmp/sage_harness.whl\n",
        "httpx @ https://example.invalid/httpx.whl\n",
    ],
)
def test_lock_manifest_rejects_editable_local_and_url_requirements(
    tmp_path: Path, requirement: str
) -> None:
    lock = tmp_path / "requirements-lock.txt"
    lock.write_text(requirement, encoding="utf-8")

    with pytest.raises(BuildEnvironmentError, match="immutable name==version"):
        parse_lock_manifest(lock)


def test_environment_manifest_fails_closed_on_drift_extra_or_direct_url() -> None:
    expected = {"fastapi": "0.115.6", "pyinstaller": "6.16.0"}

    with pytest.raises(BuildEnvironmentError, match="does not match lock"):
        verify_environment_manifest(
            expected,
            {"fastapi": "0.115.7", "pyinstaller": "6.16.0"},
            direct_url_distributions=(),
        )
    with pytest.raises(BuildEnvironmentError, match="does not match lock"):
        verify_environment_manifest(
            expected,
            {**expected, "unlocked-package": "1.0.0"},
            direct_url_distributions=(),
        )
    with pytest.raises(BuildEnvironmentError, match="direct URL"):
        verify_environment_manifest(
            expected,
            expected,
            direct_url_distributions=("fastapi",),
        )


def test_build_receipt_is_relative_deterministic_and_secret_free(tmp_path: Path) -> None:
    artifact = tmp_path / "sage-api-aarch64-apple-darwin"
    artifact.mkdir()
    (artifact / "sage-api-aarch64-apple-darwin").write_bytes(b"sidecar")
    environment = {
        "lock_sha256": "a" * 64,
        "manifest": {"pyinstaller": "6.16.0", "sage-harness": "0.1.0"},
        "harness": {
            "name": "sage-harness",
            "version": "0.1.0",
            "wheel": "sage_harness-0.1.0-py3-none-any.whl",
            "wheel_sha256": "b" * 64,
            "source_sha256": "c" * 64,
        },
    }

    first = build_receipt(
        artifact_dir=artifact,
        source_sha="abc123",
        source_dirty=False,
        build_environment=environment,
        smoke={"live": "passed", "ready": "passed"},
    )
    second = build_receipt(
        artifact_dir=artifact,
        source_sha="abc123",
        source_dirty=False,
        build_environment=environment,
        smoke={"live": "passed", "ready": "passed"},
    )

    assert first == second
    assert first["schema_version"] == 1
    assert first["source_sha"] == "abc123"
    assert first["source_dirty"] is False
    assert len(first["dependency_lock_sha256"]) == 64
    assert first["build_environment"] == environment
    assert first["artifact"]["name"] == "sage-api-aarch64-apple-darwin"
    assert first["artifact"]["files"][0]["path"] == "sage-api-aarch64-apple-darwin"
    serialized = json.dumps(first, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert ".env" not in serialized


def test_artifact_hygiene_rejects_env_files_and_absolute_development_paths(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / ".env").write_text("PROVIDER_KEY=private", encoding="utf-8")

    with pytest.raises(ArtifactHygieneError, match="forbidden file"):
        verify_artifact_hygiene(artifact, forbidden_roots=(Path.cwd(),))

    (artifact / ".env").unlink()
    (artifact / "binary").write_bytes(f"prefix:{Path.cwd()}".encode())
    with pytest.raises(ArtifactHygieneError, match="development path"):
        verify_artifact_hygiene(artifact, forbidden_roots=(Path.cwd(),))


@pytest.mark.parametrize(
    ("filename", "contents", "message"),
    [
        (
            "config.json",
            b'{"api_key":"sk-live-secret-sentinel-1234567890"}',
            "secret material",
        ),
        (
            "identity.pem",
            b"-----BEGIN PRIVATE KEY-----\nprivate-sentinel\n-----END PRIVATE KEY-----\n",
            "private key",
        ),
        (
            "direct_url.json",
            b'{"url":"file:///private/tmp/sage-harness"}',
            "direct_url",
        ),
        ("settings.json", b"{}", "resource is not allowlisted"),
    ],
)
def test_artifact_hygiene_rejects_secret_and_unapproved_resources(
    tmp_path: Path, filename: str, contents: bytes, message: str
) -> None:
    artifact = tmp_path / "artifact"
    internal = artifact / "_internal"
    internal.mkdir(parents=True)
    (artifact / "artifact").write_bytes(b"sidecar")
    (internal / filename).write_bytes(contents)

    with pytest.raises(ArtifactHygieneError, match=message):
        verify_artifact_hygiene(artifact, forbidden_roots=())


@pytest.mark.parametrize("live_mode", ["404", "wrong-schema"])
def test_packaged_smoke_rejects_missing_or_invalid_liveness(tmp_path: Path, live_mode: str) -> None:
    executable = _fake_sidecar(tmp_path, live_mode=live_mode)

    with pytest.raises(RuntimeError, match="liveness"):
        smoke_packaged_artifact(executable, source_sha="fake-sha", timeout=3)


def test_packaged_smoke_accepts_additive_health_fields(tmp_path: Path) -> None:
    executable = _fake_sidecar(
        tmp_path,
        live_mode="ready",
        additive_fields=True,
    )

    smoke = smoke_packaged_artifact(executable, source_sha="fake-sha", timeout=3)

    assert smoke["liveness"] == "passed"
    assert smoke["readiness"] == "passed"


@pytest.mark.parametrize(
    "ready_mode",
    ["missing-check", "blocked-check", "wrong-version-type"],
)
def test_packaged_smoke_rejects_missing_or_invalid_required_readiness_check(
    tmp_path: Path, ready_mode: str
) -> None:
    executable = _fake_sidecar(
        tmp_path,
        live_mode="ready",
        ready_mode=ready_mode,
    )

    with pytest.raises(RuntimeError, match="readiness"):
        smoke_packaged_artifact(executable, source_sha="fake-sha", timeout=3)


def test_packaged_smoke_detects_and_cleans_orphaned_grandchild(tmp_path: Path) -> None:
    executable = _fake_sidecar(tmp_path, live_mode="ready", spawn_orphan=True)

    with pytest.raises(RuntimeError, match="process group or descendant"):
        smoke_packaged_artifact(executable, source_sha="fake-sha", timeout=3)

    orphan_pid = int((tmp_path / "orphan.pid").read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(orphan_pid, 0)


def test_packaged_smoke_cleans_process_when_cleanup_snapshot_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_sidecar(tmp_path, live_mode="ready", spawn_orphan=True)
    real_snapshot = sidecar_build._process_snapshot
    snapshot_calls = 0

    def flaky_snapshot() -> dict[int, sidecar_build._ProcessState]:
        nonlocal snapshot_calls
        snapshot_calls += 1
        if snapshot_calls >= 2:
            raise subprocess.CalledProcessError(1, "/bin/ps")
        return real_snapshot()

    monkeypatch.setattr(sidecar_build, "_process_snapshot", flaky_snapshot)

    with pytest.raises(ExceptionGroup, match="smoke and cleanup failed"):
        smoke_packaged_artifact(executable, source_sha="fake-sha", timeout=3)

    sidecar_pid = int((tmp_path / "sidecar.pid").read_text(encoding="utf-8"))
    orphan_pid = int((tmp_path / "orphan.pid").read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(sidecar_pid, 0)
    with pytest.raises(ProcessLookupError):
        os.kill(orphan_pid, 0)


def test_packaged_smoke_finishes_cleanup_when_both_waits_time_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_sidecar(tmp_path, live_mode="ready")
    real_wait = subprocess.Popen.wait
    wait_calls = 0

    def flaky_wait(process: subprocess.Popen[str], timeout: float | None = None) -> int:
        nonlocal wait_calls
        args = process.args
        executable_arg = args[0] if isinstance(args, list | tuple) else args
        if executable_arg != str(executable):
            return real_wait(process, timeout)
        wait_calls += 1
        if wait_calls <= 2:
            raise subprocess.TimeoutExpired(process.args, timeout or 0)
        return real_wait(process, timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", flaky_wait)

    with pytest.raises(ExceptionGroup, match="smoke and cleanup failed"):
        smoke_packaged_artifact(executable, source_sha="fake-sha", timeout=3)

    sidecar_pid = int((tmp_path / "sidecar.pid").read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(sidecar_pid, 0)


def test_packaged_smoke_preserves_protocol_and_cleanup_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = _fake_sidecar(tmp_path, live_mode="ready")

    def protocol_failure(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("protocol sentinel")

    def audit_failure() -> dict[int, sidecar_build._ProcessState]:
        raise subprocess.CalledProcessError(1, "/bin/ps")

    monkeypatch.setattr(sidecar_build, "_validate_liveness", protocol_failure)
    monkeypatch.setattr(sidecar_build, "_process_snapshot", audit_failure)

    with pytest.raises(ExceptionGroup) as exc_info:
        smoke_packaged_artifact(executable, source_sha="fake-sha", timeout=3)

    messages = [str(error) for error in exc_info.value.exceptions]
    assert "protocol sentinel" in messages
    assert messages.count("packaged sidecar cleanup audit failed") == 1
    assert messages.count("packaged sidecar final cleanup audit failed") == 1
    sidecar_pid = int((tmp_path / "sidecar.pid").read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(sidecar_pid, 0)
