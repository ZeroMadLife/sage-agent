"""Build and verify the macOS arm64 PyInstaller one-dir sidecar."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import selectors
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import urlopen

ARTIFACT_NAME = "sage-api-aarch64-apple-darwin"
RECEIPT_NAME = "build-receipt.json"
RECEIPT_SCHEMA_VERSION = 1
DEPENDENCY_LOCK = Path(__file__).with_name("requirements-lock.txt")
DEPENDENCIES = (
    "aiosqlite",
    "cryptography",
    "fastapi",
    "httpx",
    "langgraph",
    "langgraph-checkpoint-sqlite",
    "orjson",
    "psycopg2-binary",
    "pydantic",
    "sage-harness",
    "tenacity",
    "uvicorn",
)
FORBIDDEN_ARTIFACT_NAMES = frozenset(
    {
        ".env",
        "provider-key",
        "provider_key",
        "sage.sqlite3",
        "checkpoints.sqlite3",
    }
)


class ArtifactHygieneError(RuntimeError):
    """The distributable contains local state or a development path."""


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
        if lowered in FORBIDDEN_ARTIFACT_NAMES or lowered.startswith(".env."):
            relative = path.relative_to(artifact_dir)
            raise ArtifactHygieneError(f"forbidden file in artifact: {relative}")
        if _contains_bytes(path, needles):
            relative = path.relative_to(artifact_dir)
            raise ArtifactHygieneError(f"development path embedded in artifact: {relative}")


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for distribution in DEPENDENCIES:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "not-installed"
    return versions


def build_receipt(
    *,
    artifact_dir: Path,
    source_sha: str,
    source_dirty: bool,
    pyinstaller_version: str,
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
        "pyinstaller_version": pyinstaller_version,
        "dependency_lock_sha256": _sha256(DEPENDENCY_LOCK),
        "dependencies": _dependency_versions(),
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


def _wait_for_ready(port: int, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urlopen(f"http://127.0.0.1:{port}/health/ready", timeout=1) as response:
                payload = json.load(response)
            if isinstance(payload, dict):
                return payload
            raise RuntimeError("packaged readiness response is not an object")
        except HTTPError as exc:
            if exc.code == 503:
                payload = json.load(exc)
                if isinstance(payload, dict):
                    return payload
            raise
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError("packaged sidecar did not become ready") from None
            time.sleep(0.05)


def _child_process_ids(parent_pid: int) -> tuple[int, ...]:
    result = subprocess.run(
        ["/bin/ps", "-axo", "ppid=,pid="],
        check=True,
        capture_output=True,
        text=True,
    )
    children: list[int] = []
    for raw_line in result.stdout.splitlines():
        fields = raw_line.split()
        if len(fields) == 2 and int(fields[0]) == parent_pid:
            children.append(int(fields[1]))
    return tuple(children)


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


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
            stderr=subprocess.PIPE,
            text=True,
        )
        stderr = ""
        try:
            startup = _read_startup_receipt(process, timeout)
            if startup.get("build_sha") != source_sha:
                raise RuntimeError("packaged sidecar build SHA does not match the receipt")
            port = startup.get("port")
            if not isinstance(port, int) or port <= 0:
                raise RuntimeError("packaged sidecar did not bind an OS-assigned port")
            ready = _wait_for_ready(port, timeout)
            if ready.get("status") != "ready":
                raise RuntimeError("packaged sidecar readiness smoke was blocked")
            checks = ready.get("checks")
            if not isinstance(checks, dict):
                raise RuntimeError("packaged sidecar readiness checks are missing")
            child_processes = _child_process_ids(process.pid)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                _, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                _, stderr = process.communicate()
                raise RuntimeError("packaged sidecar did not terminate gracefully") from None
        if process.returncode != 0:
            raise RuntimeError(
                f"packaged sidecar exited with {process.returncode}: {stderr[-1000:]}"
            )
        orphaned = [pid for pid in child_processes if _process_exists(pid)]
        if orphaned:
            raise RuntimeError(f"packaged sidecar left child processes: {orphaned}")
        expected_checks = ("storage", "checkpoint", "tls", "core_imports")
        failed = [name for name in expected_checks if checks.get(name, {}).get("status") != "ready"]
        if failed:
            raise RuntimeError(f"packaged sidecar checks failed: {', '.join(failed)}")
        if not (data_dir / "sage.sqlite3").is_file():
            raise RuntimeError("packaged sidecar did not create the SQLite store")
        if not (data_dir / "checkpoints.sqlite3").is_file():
            raise RuntimeError("packaged sidecar did not create the checkpoint store")
    return {
        "child_process_cleanup": "passed",
        "core_imports": "passed",
        "graceful_exit": "passed",
        "live_ready": "passed",
        "random_loopback": "passed",
        "sqlite_checkpoint_reopen": "passed",
        "tls_client": "passed",
    }


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
    dist_dir = output_dir.resolve() / "dist"
    work_dir = output_dir.resolve() / "work"
    subprocess.run(
        [
            sys.executable,
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
    )
    artifact_dir = dist_dir / ARTIFACT_NAME
    executable = artifact_dir / ARTIFACT_NAME
    if not executable.is_file():
        raise RuntimeError(f"PyInstaller artifact is missing: {executable}")

    verify_artifact_hygiene(
        artifact_dir,
        forbidden_roots=(root, output_dir.resolve(), Path.home()),
    )
    source_sha, source_dirty = _source_state(root)
    pyinstaller_version = importlib.metadata.version("pyinstaller")
    receipt_path = artifact_dir / RECEIPT_NAME
    pending_receipt = build_receipt(
        artifact_dir=artifact_dir,
        source_sha=source_sha,
        source_dirty=source_dirty,
        pyinstaller_version=pyinstaller_version,
        smoke={"status": "pending"},
    )
    receipt_path.write_text(
        json.dumps(pending_receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    smoke = smoke_packaged_artifact(executable, source_sha=source_sha)
    final_receipt = build_receipt(
        artifact_dir=artifact_dir,
        source_sha=source_sha,
        source_dirty=source_dirty,
        pyinstaller_version=pyinstaller_version,
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
