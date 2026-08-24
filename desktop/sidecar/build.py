"""Build and verify the macOS arm64 PyInstaller one-dir sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import venv
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


@contextmanager
def _local_stub_provider() -> Iterator[str]:
    """Serve deterministic OpenAI-compatible responses on an isolated loopback port."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    model_calls = [0]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/v1/models":
                self.send_error(404)
                return
            self._json({"object": "list", "data": [{"id": "model-smoke"}]})

        def do_POST(self) -> None:
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            model_calls[0] += 1
            if model_calls[0] == 2:
                content = (
                    '<tool>{"name":"run_shell","args":{"command":"touch '
                    'artifact-side-effect-must-not-exist"}}</tool>'
                )
            elif model_calls[0] > 2:
                content = "<final>side effect remained blocked</final>"
            else:
                content = "<final>artifact model turn passed</final>"
            if payload.get("stream"):
                chunks = [
                    {
                        "id": "chatcmpl-sage-smoke",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "model-smoke",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": content},
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        "id": "chatcmpl-sage-smoke",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "model-smoke",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    },
                ]
                body = (
                    b"".join(f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks)
                    + b"data: [DONE]\n\n"
                )
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self._json(
                {
                    "id": "chatcmpl-sage-smoke",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "model-smoke",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )

        def _json(self, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


ARTIFACT_NAME = "sage-api-aarch64-apple-darwin"
RECEIPT_NAME = "build-receipt.json"
RECEIPT_SCHEMA_VERSION = 1
DEPENDENCY_LOCK = Path(__file__).with_name("requirements-lock.txt")
FORBIDDEN_ARTIFACT_NAMES = frozenset(
    {
        ".env",
        "provider-key",
        "provider_key",
        "sage.sqlite3",
        "checkpoints.sqlite3",
    }
)
CONFIG_RESOURCE_SUFFIXES = frozenset({".cfg", ".conf", ".ini", ".json", ".toml", ".yaml", ".yml"})
PRIVATE_KEY_PATTERN = re.compile(rb"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----")
SECRET_TOKEN_PATTERN = re.compile(rb"\bsk-(?:live|prod|secret|test)-[A-Za-z0-9_-]{12,}\b")
SECRET_ASSIGNMENT_PATTERN = re.compile(
    rb"""(?ix)
    ["']?(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|private[_-]?key|secret)["']?
    \s*[:=]\s*["']?[A-Za-z0-9_./+=:-]{8,}
    """
)


class ArtifactHygieneError(RuntimeError):
    """The distributable contains local state or a development path."""


class BuildEnvironmentError(RuntimeError):
    """The isolated build environment does not match its immutable inputs."""


@dataclass(frozen=True)
class BuildEnvironmentEvidence:
    """Verified isolated interpreter plus immutable inputs used by PyInstaller."""

    python_executable: Path
    lock_sha256: str
    manifest: dict[str, str]
    harness_name: str
    harness_version: str
    harness_wheel: str
    harness_wheel_sha256: str
    harness_source_sha256: str

    def as_receipt(self) -> dict[str, Any]:
        return {
            "lock_sha256": self.lock_sha256,
            "manifest": dict(sorted(self.manifest.items())),
            "harness": {
                "name": self.harness_name,
                "version": self.harness_version,
                "wheel": self.harness_wheel,
                "wheel_sha256": self.harness_wheel_sha256,
                "source_sha256": self.harness_source_sha256,
            },
        }


def _canonical_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock_manifest(path: Path) -> dict[str, str]:
    """Parse a lock containing only immutable PEP 503 name/version pins."""
    manifest: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([^\s;]+)", line)
        if match is None:
            raise BuildEnvironmentError(f"lock line {line_number} must use immutable name==version")
        name = _canonical_distribution_name(match.group(1))
        version = match.group(2)
        if name in manifest:
            raise BuildEnvironmentError(f"duplicate distribution in lock: {name}")
        manifest[name] = version
    if not manifest:
        raise BuildEnvironmentError("dependency lock must not be empty")
    return manifest


def verify_environment_manifest(
    expected: Mapping[str, str],
    actual: Mapping[str, str],
    *,
    direct_url_distributions: Sequence[str],
) -> None:
    """Fail closed on dependency drift, extras or URL-based installations."""
    direct_urls = tuple(
        sorted(_canonical_distribution_name(name) for name in direct_url_distributions)
    )
    if direct_urls:
        raise BuildEnvironmentError(
            f"build environment contains direct URL distributions: {', '.join(direct_urls)}"
        )
    normalized_expected = {
        _canonical_distribution_name(name): version for name, version in expected.items()
    }
    normalized_actual = {
        _canonical_distribution_name(name): version for name, version in actual.items()
    }
    if normalized_actual != normalized_expected:
        raise BuildEnvironmentError("installed distribution manifest does not match lock")


def _source_tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    files = [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    ]
    for path in files:
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _inspect_environment(python_executable: Path) -> tuple[dict[str, str], tuple[str, ...]]:
    script = """
import importlib.metadata
import json

items = []
direct_urls = []
for distribution in importlib.metadata.distributions():
    name = distribution.metadata.get("Name")
    if not name:
        continue
    items.append([name, distribution.version])
    if distribution.read_text("direct_url.json") is not None:
        direct_urls.append(name)
print(json.dumps({"items": items, "direct_urls": direct_urls}, sort_keys=True))
"""
    result = subprocess.run(
        [str(python_executable), "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    manifest: dict[str, str] = {}
    for raw_name, raw_version in payload["items"]:
        name = _canonical_distribution_name(str(raw_name))
        if name in manifest:
            raise BuildEnvironmentError(f"duplicate installed distribution: {name}")
        manifest[name] = str(raw_version)
    direct_urls = tuple(str(name) for name in payload["direct_urls"])
    return manifest, direct_urls


def prepare_build_environment(root: Path, environment_dir: Path) -> BuildEnvironmentEvidence:
    """Create and prove the isolated lock-matched environment used for packaging."""
    lock_path = root / "desktop" / "sidecar" / "requirements-lock.txt"
    expected = parse_lock_manifest(lock_path)
    venv.EnvBuilder(with_pip=True, clear=False, symlinks=True).create(environment_dir)
    python_executable = environment_dir / "bin" / "python"
    subprocess.run(
        [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-deps",
            "--requirement",
            str(lock_path),
        ],
        cwd=root,
        check=True,
    )
    manifest, direct_urls = _inspect_environment(python_executable)
    verify_environment_manifest(
        expected,
        manifest,
        direct_url_distributions=direct_urls,
    )

    harness_root = root / "packages" / "sage_harness"
    harness_config = tomllib.loads((harness_root / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    harness_name = _canonical_distribution_name(str(harness_config["name"]))
    harness_version = str(harness_config["version"])
    wheel_dir = environment_dir / "wheelhouse"
    wheel_dir.mkdir()
    subprocess.run(
        [
            str(python_executable),
            "-m",
            "pip",
            "wheel",
            "--disable-pip-version-check",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
            str(harness_root),
        ],
        cwd=root,
        check=True,
    )
    wheels = tuple(wheel_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise BuildEnvironmentError("Harness build must produce exactly one wheel")
    wheel = wheels[0]
    subprocess.run(
        [
            str(python_executable),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--no-deps",
            "--find-links",
            str(wheel_dir),
            f"{harness_name}=={harness_version}",
        ],
        cwd=root,
        check=True,
    )
    installed, installed_direct_urls = _inspect_environment(python_executable)
    expected_with_harness = {**expected, harness_name: harness_version}
    verify_environment_manifest(
        expected_with_harness,
        installed,
        direct_url_distributions=installed_direct_urls,
    )
    return BuildEnvironmentEvidence(
        python_executable=python_executable,
        lock_sha256=_sha256(lock_path),
        manifest=installed,
        harness_name=harness_name,
        harness_version=harness_version,
        harness_wheel=wheel.name,
        harness_wheel_sha256=_sha256(wheel),
        harness_source_sha256=_source_tree_hash(harness_root),
    )


def _artifact_files(artifact_dir: Path) -> Iterator[Path]:
    for path in sorted(artifact_dir.rglob("*")):
        if path.is_file() and path.name != RECEIPT_NAME:
            yield path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _contains_bytes(path: Path, needles: tuple[bytes, ...]) -> bool:
    if not needles:
        return False
    overlap = max(len(needle) for needle in needles) - 1
    previous = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            payload = previous + chunk
            if any(needle in payload for needle in needles):
                return True
            previous = payload[-overlap:] if overlap > 0 else b""
    return False


def _matches_pattern(path: Path, pattern: re.Pattern[bytes]) -> bool:
    previous = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            payload = previous + chunk
            if pattern.search(payload) is not None:
                return True
            previous = payload[-4096:]
    return False


def _is_allowlisted_config_resource(path: Path, artifact_dir: Path) -> bool:
    relative = path.relative_to(artifact_dir).as_posix()
    return relative == "_internal/config/coding_models.toml" or bool(
        re.fullmatch(
            r"_internal/cryptography-[^/]+\.dist-info/sboms/[^/]+\.json",
            relative,
        )
    )


def verify_artifact_hygiene(
    artifact_dir: Path,
    *,
    forbidden_roots: tuple[Path, ...],
) -> None:
    """Reject local configuration, user stores and absolute development roots."""
    needles = tuple(
        str(path.expanduser().resolve()).encode() for path in forbidden_roots if str(path).strip()
    )
    for path in _artifact_files(artifact_dir):
        lowered = path.name.lower()
        relative = path.relative_to(artifact_dir)
        if lowered in FORBIDDEN_ARTIFACT_NAMES or lowered.startswith(".env."):
            raise ArtifactHygieneError(f"forbidden file in artifact: {relative}")
        if lowered == "direct_url.json":
            raise ArtifactHygieneError(f"direct_url metadata in artifact: {relative}")
        if _matches_pattern(path, PRIVATE_KEY_PATTERN):
            raise ArtifactHygieneError(f"private key material in artifact: {relative}")
        if _matches_pattern(path, SECRET_TOKEN_PATTERN):
            raise ArtifactHygieneError(f"secret material in artifact: {relative}")
        if path.suffix.lower() in CONFIG_RESOURCE_SUFFIXES:
            if _matches_pattern(path, SECRET_ASSIGNMENT_PATTERN):
                raise ArtifactHygieneError(f"secret material in artifact: {relative}")
            if not _is_allowlisted_config_resource(path, artifact_dir):
                raise ArtifactHygieneError(f"resource is not allowlisted in artifact: {relative}")
        if _contains_bytes(path, needles):
            raise ArtifactHygieneError(f"development path embedded in artifact: {relative}")


def build_receipt(
    *,
    artifact_dir: Path,
    source_sha: str,
    source_dirty: bool,
    build_environment: Mapping[str, Any],
    smoke: Mapping[str, str],
) -> dict[str, Any]:
    """Return a deterministic receipt whose paths are artifact-relative."""
    files = [
        {
            "path": str(path.relative_to(artifact_dir)),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in _artifact_files(artifact_dir)
    ]
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "source_sha": source_sha,
        "source_dirty": source_dirty,
        "target": "aarch64-apple-darwin",
        "python_version": platform.python_version(),
        "dependency_lock_sha256": _sha256(DEPENDENCY_LOCK),
        "build_environment": dict(build_environment),
        "artifact": {"name": artifact_dir.name, "files": files},
        "smoke": dict(sorted(smoke.items())),
    }


def _read_startup_receipt(process: subprocess.Popen[str], timeout: float) -> dict[str, Any]:
    if process.stdout is None:
        raise RuntimeError("packaged sidecar stdout is unavailable")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        if not selector.select(timeout):
            raise TimeoutError("packaged sidecar did not emit its startup receipt")
        line = process.stdout.readline()
    finally:
        selector.close()
    if not line:
        raise RuntimeError("packaged sidecar exited before its startup receipt")
    payload = json.loads(line)
    if not isinstance(payload, dict):
        raise RuntimeError("packaged sidecar emitted an invalid startup receipt")
    return payload


def _wait_for_health(port: int, path: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as response:
                payload = json.load(response)
            if isinstance(payload, dict):
                return payload
            raise RuntimeError(f"packaged health response is not an object: {path}")
        except HTTPError as exc:
            if path == "/health/ready" and exc.code == 503:
                payload = json.load(exc)
                if isinstance(payload, dict):
                    return payload
            label = "liveness" if path == "/health/live" else "readiness"
            raise RuntimeError(f"packaged {label} endpoint returned HTTP {exc.code}") from exc
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"packaged sidecar health timed out: {path}") from None
            time.sleep(0.05)


def _validate_liveness(payload: Mapping[str, Any], *, source_sha: str) -> None:
    expected = {
        "status": "live",
        "profile": "desktop-minimal",
        "api_version": "1",
        "build_sha": source_sha,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise RuntimeError("packaged liveness schema is invalid")


def _validate_readiness(payload: Mapping[str, Any], *, source_sha: str) -> dict[str, Any]:
    expected_identity = {
        "status": "ready",
        "profile": "desktop-minimal",
        "api_version": "1",
        "build_sha": source_sha,
    }
    if any(payload.get(key) != value for key, value in expected_identity.items()):
        raise RuntimeError("packaged readiness identity is invalid")
    checks = payload.get("checks")
    if not isinstance(checks, dict):
        raise RuntimeError("packaged readiness checks are missing")
    expected_checks = {
        "api",
        "build",
        "schema",
        "storage",
        "checkpoint",
        "tls",
        "core_imports",
    }
    missing_checks = expected_checks.difference(checks)
    if missing_checks:
        raise RuntimeError("packaged readiness check schema is invalid")
    for name in expected_checks:
        check = checks[name]
        if (
            not isinstance(check, dict)
            or check.get("status") != "ready"
            or not isinstance(check.get("version"), str)
            or not check["version"]
        ):
            raise RuntimeError(f"packaged readiness check is invalid: {name}")
    return checks


@dataclass(frozen=True)
class _ProcessState:
    parent_pid: int
    process_group_id: int
    state: str


def _process_snapshot() -> dict[int, _ProcessState]:
    result = subprocess.run(
        ["/bin/ps", "-axo", "pid=,ppid=,pgid=,state="],
        check=True,
        capture_output=True,
        text=True,
    )
    processes: dict[int, _ProcessState] = {}
    for raw_line in result.stdout.splitlines():
        fields = raw_line.split()
        if len(fields) == 4:
            pid, parent_pid, process_group_id = map(int, fields[:3])
            processes[pid] = _ProcessState(
                parent_pid=parent_pid,
                process_group_id=process_group_id,
                state=fields[3],
            )
    return processes


def _descendant_process_ids(
    parent_pid: int,
    processes: Mapping[int, _ProcessState],
) -> set[int]:
    descendants: set[int] = set()
    frontier = {parent_pid}
    while frontier:
        children = {
            pid
            for pid, process in processes.items()
            if process.parent_pid in frontier and pid not in descendants
        }
        descendants.update(children)
        frontier = children
    return descendants


def _remaining_process_ids(
    process_group_id: int,
    tracked_descendants: set[int],
) -> tuple[int, ...]:
    processes = _process_snapshot()
    return tuple(
        sorted(
            pid
            for pid, process in processes.items()
            if process.process_group_id == process_group_id or pid in tracked_descendants
        )
    )


def _wait_for_process_cleanup(
    process_group_id: int,
    tracked_descendants: set[int],
    *,
    timeout: float,
) -> tuple[int, ...]:
    deadline = time.monotonic() + timeout
    stable_empty_samples = 0
    while True:
        remaining = _remaining_process_ids(process_group_id, tracked_descendants)
        if not remaining:
            stable_empty_samples += 1
            if stable_empty_samples >= 3:
                return ()
        else:
            stable_empty_samples = 0
        if time.monotonic() >= deadline:
            return remaining
        time.sleep(0.05)


def _signal_process_tree(
    process_group_id: int,
    tracked_descendants: set[int],
    signal_number: signal.Signals,
) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process_group_id, signal_number)
    for pid in tracked_descendants:
        with suppress(ProcessLookupError):
            os.kill(pid, signal_number)


def smoke_packaged_artifact(
    executable: Path,
    *,
    source_sha: str,
    timeout: float = 30,
) -> dict[str, str]:
    """Run the standalone artifact without repository or virtualenv imports."""
    env = {
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PATH": "/usr/bin:/bin",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    with tempfile.TemporaryDirectory(prefix="sage-sidecar-smoke-") as temporary:
        data_dir = Path(temporary) / "data"
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_stream:
            process = subprocess.Popen(
                [
                    str(executable),
                    "--bind",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--data-dir",
                    str(data_dir),
                ],
                cwd=temporary,
                env=env,
                stdout=subprocess.PIPE,
                stderr=stderr_stream,
                start_new_session=True,
                text=True,
            )
            process_group_id = process.pid
            tracked_descendants: set[int] = set()
            failure: Exception | None = None
            cleanup_failures: list[Exception] = []
            try:
                startup = _read_startup_receipt(process, timeout)
                if process.stdout is not None:
                    process.stdout.close()
                if startup.get("build_sha") != source_sha:
                    raise RuntimeError("packaged sidecar build SHA does not match the receipt")
                port = startup.get("port")
                if not isinstance(port, int) or port <= 0:
                    raise RuntimeError("packaged sidecar did not bind an OS-assigned port")
                live = _wait_for_health(port, "/health/live", timeout)
                _validate_liveness(live, source_sha=source_sha)
                ready = _wait_for_health(port, "/health/ready", timeout)
                _validate_readiness(ready, source_sha=source_sha)
                tracked_descendants.update(
                    _descendant_process_ids(process.pid, _process_snapshot())
                )
            except Exception as exc:
                failure = exc
            finally:
                audit_unknown = False
                try:
                    tracked_descendants.update(
                        _descendant_process_ids(process.pid, _process_snapshot())
                    )
                except Exception as exc:
                    audit_unknown = True
                    cleanup_error = RuntimeError("packaged sidecar cleanup audit failed")
                    cleanup_error.__cause__ = exc
                    cleanup_failures.append(cleanup_error)
                needs_kill = audit_unknown
                if process.poll() is None:
                    try:
                        _signal_process_tree(
                            process_group_id,
                            tracked_descendants,
                            signal.SIGTERM,
                        )
                    except Exception as exc:
                        needs_kill = True
                        cleanup_error = RuntimeError("packaged sidecar SIGTERM failed")
                        cleanup_error.__cause__ = exc
                        cleanup_failures.append(cleanup_error)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired as exc:
                    needs_kill = True
                    cleanup_error = RuntimeError("packaged sidecar did not terminate gracefully")
                    cleanup_error.__cause__ = exc
                    cleanup_failures.append(cleanup_error)
                if not needs_kill:
                    try:
                        remaining = _wait_for_process_cleanup(
                            process_group_id,
                            tracked_descendants,
                            timeout=0.5,
                        )
                    except Exception as exc:
                        audit_unknown = True
                        needs_kill = True
                        cleanup_error = RuntimeError("packaged sidecar cleanup audit failed")
                        cleanup_error.__cause__ = exc
                        cleanup_failures.append(cleanup_error)
                    else:
                        if remaining:
                            needs_kill = True
                            cleanup_failures.append(
                                RuntimeError(
                                    "packaged sidecar left process group or descendant "
                                    f"processes: {list(remaining)}"
                                )
                            )
                if needs_kill:
                    try:
                        _signal_process_tree(
                            process_group_id,
                            tracked_descendants,
                            signal.SIGKILL,
                        )
                    except Exception as exc:
                        cleanup_error = RuntimeError("packaged sidecar SIGKILL failed")
                        cleanup_error.__cause__ = exc
                        cleanup_failures.append(cleanup_error)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired as exc:
                        cleanup_error = RuntimeError(
                            "packaged sidecar remained alive after SIGKILL"
                        )
                        cleanup_error.__cause__ = exc
                        cleanup_failures.append(cleanup_error)
                    process.poll()
                    try:
                        final_remaining = _wait_for_process_cleanup(
                            process_group_id, tracked_descendants, timeout=5
                        )
                    except Exception as exc:
                        cleanup_error = RuntimeError("packaged sidecar final cleanup audit failed")
                        cleanup_error.__cause__ = exc
                        cleanup_failures.append(cleanup_error)
                    else:
                        if final_remaining:
                            cleanup_failures.append(
                                RuntimeError(
                                    "packaged sidecar still has process group or descendant "
                                    f"processes after SIGKILL: {list(final_remaining)}"
                                )
                            )
                    with suppress(subprocess.TimeoutExpired):
                        process.wait(timeout=0)
                try:
                    stderr_stream.seek(0)
                    stderr = stderr_stream.read()
                finally:
                    if process.stdout is not None:
                        process.stdout.close()
            failures = ([failure] if failure is not None else []) + cleanup_failures
            if len(failures) == 1:
                raise failures[0]
            if failures:
                raise ExceptionGroup("packaged sidecar smoke and cleanup failed", failures)
        if process.returncode != 0:
            raise RuntimeError(
                f"packaged sidecar exited with {process.returncode}: {stderr[-1000:]}"
            )
        if not (data_dir / "sage.sqlite3").is_file():
            raise RuntimeError("packaged sidecar did not create the SQLite store")
        if not (data_dir / "checkpoints.sqlite3").is_file():
            raise RuntimeError("packaged sidecar did not create the checkpoint store")
    return {
        "child_process_cleanup": "passed",
        "core_imports": "passed",
        "graceful_exit": "passed",
        "liveness": "passed",
        "random_loopback": "passed",
        "readiness": "passed",
        "sqlite_checkpoint_reopen": "passed",
        "tls_client": "passed",
    }


def smoke_packaged_product(
    executable: Path,
    *,
    source_sha: str,
    timeout: float = 30,
) -> dict[str, str]:
    """Start the frozen product profile through its inherited bootstrap pipe."""
    env = {
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PATH": "/usr/bin:/bin",
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }
    secret = "test-secret-packaged-product-smoke"
    bearer = "test-bearer-packaged-product-smoke"
    executed_assertions: list[str] = []
    with (
        _local_stub_provider() as provider_base_url,
        tempfile.TemporaryDirectory(prefix="sage-sidecar-product-smoke-") as temporary,
    ):
        root = Path(temporary)
        data_dir = root / "data"
        workspace = root / "workspace"
        workspace.mkdir()
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_stream:
            process = subprocess.Popen(
                [str(executable), "--desktop-host"],
                cwd=temporary,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_stream,
                start_new_session=True,
                text=True,
            )
            try:
                if process.stdin is None:
                    raise RuntimeError("packaged product sidecar stdin is unavailable")
                process.stdin.write(
                    json.dumps(
                        {
                            "instance_id": "packaged-product-smoke",
                            "nonce": "packaged-product-nonce",
                            "bearer": bearer,
                            "origin": "tauri://localhost",
                            "data_dir": str(data_dir),
                            "runtime": {
                                "workspace_path": str(workspace),
                                "provider": {
                                    "provider_id": "packaged-product-provider",
                                    "base_url": provider_base_url,
                                    "default_model": "model-smoke",
                                    "api_mode": "openai_chat_completions",
                                    "api_key": secret,
                                },
                            },
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
                process.stdin.flush()
                startup = _read_startup_receipt(process, timeout)
                if startup.get("build_sha") != source_sha:
                    raise RuntimeError("packaged product build SHA does not match the receipt")
                port = startup.get("port")
                if not isinstance(port, int) or port <= 0:
                    raise RuntimeError("packaged product did not bind an OS-assigned port")
                headers = {
                    "Authorization": f"Bearer {bearer}",
                    "Origin": "tauri://localhost",
                }
                assistant = _request_json(port, "/api/v1/assistant/home", headers=headers)
                knowledge = _request_json(port, "/api/v1/knowledge", headers=headers)
                session = _request_json(
                    port,
                    "/api/v1/coding/session",
                    headers={**headers, "Content-Type": "application/json"},
                    method="POST",
                    body=b"{}",
                )
                if assistant.get("identity", {}).get("mode") != "local":
                    raise RuntimeError("packaged product Assistant is not in local mode")
                if knowledge.get("status") != "ready":
                    raise RuntimeError("packaged product SQLite Knowledge is unavailable")
                if not isinstance(session.get("session_id"), str):
                    raise RuntimeError("packaged product conversation session is unavailable")
                session_id = str(session["session_id"])
                _exercise_packaged_model_turn(
                    port,
                    bearer=bearer,
                    origin="tauri://localhost",
                    session_id=session_id,
                    content="artifact-model-turn-probe",
                    expected_final="artifact model turn passed",
                )
                executed_assertions.append("model_turn")
                _exercise_packaged_sqlite_rag(port, headers=headers, workspace=workspace)
                executed_assertions.append("sqlite_rag")
                _assert_packaged_capability_boundaries(
                    port,
                    headers=headers,
                    bearer=bearer,
                    origin="tauri://localhost",
                    session_id=session_id,
                    workspace=workspace,
                )
                executed_assertions.append("capability_boundaries")
                try:
                    _request_json(port, "/api/v1/cloud/auth/options", headers=headers)
                except HTTPError as exc:
                    if exc.code != 404:
                        raise
                else:
                    raise RuntimeError("packaged product unexpectedly exposes Cloud OAuth")
            finally:
                if process.stdin is not None:
                    process.stdin.close()
                    process.stdin = None
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                stderr_stream.seek(0)
                stderr = stderr_stream.read()
                if process.stdout is not None:
                    process.stdout.close()
            if process.returncode != 0:
                raise RuntimeError(
                    f"packaged product exited with {process.returncode}: {stderr[-1000:]}"
                )
        if secret in stderr:
            raise RuntimeError("packaged product secret leaked to stderr")
        for path in root.rglob("*"):
            if path.is_file() and _contains_bytes(path, (secret.encode(),)):
                raise RuntimeError("packaged product secret leaked to local state")
    return {
        **_product_smoke_receipt(executed_assertions),
        "product_secret_hygiene": "passed",
    }


def _product_smoke_receipt(executed_assertions: Sequence[str]) -> dict[str, str]:
    required = {
        "model_turn": "local_conversation",
        "sqlite_rag": "local_sqlite_rag",
        "capability_boundaries": "side_effect_tools_blocked",
    }
    completed = set(executed_assertions)
    missing = sorted(set(required) - completed)
    if missing:
        raise RuntimeError(f"missing executed assertion: {missing}")
    return {
        receipt_name: "passed"
        for assertion, receipt_name in required.items()
        if assertion in completed
    }


def _exercise_packaged_model_turn(
    port: int,
    *,
    bearer: str,
    origin: str,
    session_id: str,
    content: str,
    expected_final: str,
) -> list[dict[str, Any]]:
    try:
        from websockets.sync.client import connect
        from websockets.typing import Origin, Subprotocol
    except ImportError as exc:  # pragma: no cover - enforced by the frozen lock
        raise RuntimeError("packaged product WebSocket verifier is unavailable") from exc

    events: list[dict[str, Any]] = []
    with connect(
        f"ws://127.0.0.1:{port}/api/v1/coding/{session_id}/stream?after=0",
        origin=Origin(origin),
        subprotocols=[Subprotocol("sage.v1"), Subprotocol(f"sage-bearer.{bearer}")],
        open_timeout=10,
        close_timeout=5,
    ) as websocket:
        websocket.send(json.dumps({"content": content}))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            payload = json.loads(websocket.recv(timeout=max(0.1, deadline - time.monotonic())))
            if not isinstance(payload, dict):
                raise RuntimeError("packaged model turn emitted a non-object event")
            events.append(payload)
            event_payload = payload.get("payload")
            if isinstance(event_payload, dict) and event_payload.get("event") in {
                "run_completed",
                "run_failed",
                "run_cancelled",
                "run_interrupted",
            }:
                break
        else:
            raise RuntimeError("packaged model turn did not reach a terminal event")
    finals = [
        str(payload["content"])
        for event in events
        if isinstance((payload := event.get("payload")), dict)
        and payload.get("type") == "final"
        and isinstance(payload.get("content"), str)
    ]
    if expected_final not in "".join(finals):
        event_types = [
            payload.get("type")
            for event in events
            if isinstance((payload := event.get("payload")), dict)
        ]
        raise RuntimeError(
            "packaged model turn did not return the stub Provider result; "
            f"event_types={event_types!r}, finals={finals!r}"
        )
    return events


def _exercise_packaged_sqlite_rag(
    port: int,
    *,
    headers: Mapping[str, str],
    workspace: Path,
) -> None:
    phrase = "artifact sqlite rag evidence 48271"
    (workspace / "artifact-rag.txt").write_text(f"Sage {phrase}.\n", encoding="utf-8")
    ingested = _request_json(
        port,
        "/api/v1/knowledge/ingest",
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
        body=json.dumps(
            {"source_root_id": "desktop-workspace", "relative_path": "artifact-rag.txt"}
        ).encode(),
    )
    if not isinstance(ingested.get("proposal_id"), str):
        raise RuntimeError("packaged SQLite RAG ingest did not create a proposal")
    found = _request_json(
        port,
        "/api/v1/knowledge/search",
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
        body=json.dumps({"query": phrase, "top_k": 4, "token_budget": 512}).encode(),
    )
    citations = found.get("citations")
    if not isinstance(citations, list) or not any(
        isinstance(item, dict) and item.get("source_relative_path") == "artifact-rag.txt"
        for item in citations
    ):
        raise RuntimeError("packaged SQLite RAG search did not return the ingested source")


def _assert_packaged_capability_boundaries(
    port: int,
    *,
    headers: Mapping[str, str],
    bearer: str,
    origin: str,
    session_id: str,
    workspace: Path,
) -> None:
    capabilities = _request_json(port, "/capabilities", headers=headers)
    children = capabilities.get("capabilities")
    if capabilities.get("status") != "degraded" or not isinstance(children, dict):
        raise RuntimeError("packaged capability status did not derive optional degradation")
    if children.get("conversation", {}).get("status") != "ready":
        raise RuntimeError("packaged conversation capability is not ready")
    if children.get("rag", {}).get("status") != "ready":
        raise RuntimeError("packaged RAG capability is not ready")
    if children.get("side_effect_tools", {}).get("status") != "blocked":
        raise RuntimeError("packaged side-effect capability did not fail closed")
    catalog = _request_json(
        port,
        f"/api/v1/harness/capabilities?session_id={session_id}&surface=coding&origin=local",
        headers=headers,
    )
    items = catalog.get("capabilities")
    if not isinstance(items, list):
        raise RuntimeError("packaged Harness capability catalog is unavailable")
    capability_ids = {item.get("capability_id") for item in items if isinstance(item, dict)}
    forbidden = {"local:write_file", "local:patch_file", "local:run_shell"}
    if capability_ids & forbidden:
        raise RuntimeError("packaged Harness catalog exposed side-effect tools")
    side_effect_session = _request_json(
        port,
        "/api/v1/coding/session",
        headers={**headers, "Content-Type": "application/json"},
        method="POST",
        body=b"{}",
    )
    side_effect_session_id = side_effect_session.get("session_id")
    if not isinstance(side_effect_session_id, str):
        raise RuntimeError("packaged side-effect probe session is unavailable")
    events = _exercise_packaged_model_turn(
        port,
        bearer=bearer,
        origin=origin,
        session_id=side_effect_session_id,
        content="artifact-side-effect-probe",
        expected_final="side effect remained blocked",
    )
    tool_results = [
        payload
        for event in events
        if isinstance((payload := event.get("payload")), dict)
        and payload.get("type") == "tool_result"
    ]
    if not tool_results or not any(bool(item.get("is_error")) for item in tool_results):
        raise RuntimeError("packaged direct side-effect execution did not fail closed")
    if (workspace / "artifact-side-effect-must-not-exist").exists():
        raise RuntimeError("packaged direct side-effect execution changed the workspace")


def _request_json(
    port: int,
    path: str,
    *,
    headers: Mapping[str, str],
    method: str = "GET",
    body: bytes | None = None,
) -> dict[str, Any]:
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers=dict(headers),
        method=method,
    )
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"packaged product response is invalid: {path}")
    return payload


def _source_state(root: Path) -> tuple[str, bool]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return revision.stdout.strip(), bool(status.stdout.strip())


def build(output_dir: Path) -> Path:
    """Build, inspect and smoke the D0 macOS arm64 one-dir artifact."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("D0 packaging is limited to macOS arm64")
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("D0 packaging requires Python 3.12")

    root = Path(__file__).resolve().parents[2]
    spec = root / "desktop" / "sidecar" / "sage_sidecar.spec"
    resolved_output = output_dir.resolve()
    dist_dir = resolved_output / "dist"
    work_dir = resolved_output / "work"
    with tempfile.TemporaryDirectory(prefix="sage-sidecar-build-environment-") as temporary:
        evidence = prepare_build_environment(root, Path(temporary) / "venv")
        subprocess.run(
            [
                str(evidence.python_executable),
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--distpath",
                str(dist_dir),
                "--workpath",
                str(work_dir),
                str(spec),
            ],
            cwd=root,
            check=True,
            env={
                "HOME": os.environ.get("HOME", ""),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "PATH": "/usr/bin:/bin",
                "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            },
        )
    artifact_dir = dist_dir / ARTIFACT_NAME
    executable = artifact_dir / ARTIFACT_NAME
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller artifact is missing: {executable}")

    verify_artifact_hygiene(
        artifact_dir,
        forbidden_roots=(root, resolved_output, Path.home()),
    )
    source_sha, source_dirty = _source_state(root)
    build_environment_receipt = evidence.as_receipt()
    receipt_path = artifact_dir / RECEIPT_NAME
    pending_receipt = build_receipt(
        artifact_dir=artifact_dir,
        source_sha=source_sha,
        source_dirty=source_dirty,
        build_environment=build_environment_receipt,
        smoke={"status": "pending"},
    )
    receipt_path.write_text(
        json.dumps(pending_receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    smoke = smoke_packaged_artifact(executable, source_sha=source_sha)
    smoke.update(smoke_packaged_product(executable, source_sha=source_sha))
    final_receipt = build_receipt(
        artifact_dir=artifact_dir,
        source_sha=source_sha,
        source_dirty=source_dirty,
        build_environment=build_environment_receipt,
        smoke=smoke,
    )
    receipt_path.write_text(
        json.dumps(final_receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return receipt_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("build/desktop-sidecar"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    receipt_path = build(args.output_dir)
    print(receipt_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
