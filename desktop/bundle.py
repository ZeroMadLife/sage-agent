"""Build and verify the reproducible macOS D1 desktop bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

from desktop.sidecar import build as sidecar_build

TARGET = "aarch64-apple-darwin"
REQUIRED_D0_SMOKE = frozenset(
    {
        "child_process_cleanup",
        "core_imports",
        "graceful_exit",
        "liveness",
        "random_loopback",
        "readiness",
        "sqlite_checkpoint_reopen",
        "tls_client",
    }
)


class BundleContractError(RuntimeError):
    """A build input, receipt, bundle layout or runtime proof is invalid."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _git_state(root: Path) -> tuple[str, bool]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return revision, bool(status.strip())


def _expected_manifest(root: Path) -> dict[str, str]:
    manifest = sidecar_build.parse_lock_manifest(
        root / "desktop" / "sidecar" / "requirements-lock.txt"
    )
    project = tomllib.loads(
        (root / "packages" / "sage_harness" / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    manifest[sidecar_build._canonical_distribution_name(str(project["name"]))] = str(
        project["version"]
    )
    return manifest


def validate_sidecar_receipt(
    artifact_dir: Path,
    receipt: Mapping[str, Any],
    *,
    expected_sha: str,
    expected_manifest: Mapping[str, str],
    expected_lock_sha256: str,
) -> None:
    """Fail closed unless the D0 receipt proves the exact clean source and artifact."""
    if receipt.get("schema_version") != sidecar_build.RECEIPT_SCHEMA_VERSION:
        raise BundleContractError("sidecar receipt schema is incompatible")
    expected_identity = {
        "source_sha": expected_sha,
        "source_dirty": False,
        "target": TARGET,
        "dependency_lock_sha256": expected_lock_sha256,
    }
    if any(receipt.get(key) != value for key, value in expected_identity.items()):
        raise BundleContractError("sidecar receipt identity does not match clean HEAD")

    environment = receipt.get("build_environment")
    if not isinstance(environment, Mapping):
        raise BundleContractError("sidecar build environment evidence is missing")
    if environment.get("lock_sha256") != expected_lock_sha256:
        raise BundleContractError("sidecar build lock evidence is inconsistent")
    if environment.get("manifest") != dict(sorted(expected_manifest.items())):
        raise BundleContractError("sidecar dependency manifest drifted")
    harness = environment.get("harness")
    if not isinstance(harness, Mapping) or not all(
        _is_sha256(harness.get(field)) for field in ("wheel_sha256", "source_sha256")
    ):
        raise BundleContractError("Harness wheel/source hashes are missing")

    smoke = receipt.get("smoke")
    if not isinstance(smoke, Mapping) or any(smoke.get(name) != "passed" for name in REQUIRED_D0_SMOKE):
        raise BundleContractError("sidecar smoke evidence is incomplete")

    artifact = receipt.get("artifact")
    if not isinstance(artifact, Mapping) or artifact.get("name") != sidecar_build.ARTIFACT_NAME:
        raise BundleContractError("sidecar artifact identity is invalid")
    files = artifact.get("files")
    if not isinstance(files, list):
        raise BundleContractError("sidecar artifact file manifest is missing")
    actual_paths = {
        path.relative_to(artifact_dir).as_posix()
        for path in artifact_dir.rglob("*")
        if path.is_file() and path.name != sidecar_build.RECEIPT_NAME
    }
    receipt_paths: set[str] = set()
    for item in files:
        if not isinstance(item, Mapping):
            raise BundleContractError("sidecar artifact file entry is invalid")
        relative_value = item.get("path")
        if not isinstance(relative_value, str):
            raise BundleContractError("sidecar artifact path is unsafe")
        relative = relative_value
        relative_path = PurePosixPath(relative)
        if (
            relative_path.is_absolute()
            or ".." in relative_path.parts
            or "." in relative_path.parts
        ):
            raise BundleContractError("sidecar artifact path is unsafe")
        path = artifact_dir / relative
        if not path.is_file() or path.stat().st_size != item.get("size"):
            raise BundleContractError("sidecar artifact size does not match receipt")
        if _sha256(path) != item.get("sha256"):
            raise BundleContractError("sidecar artifact hash does not match receipt")
        receipt_paths.add(relative)
    if receipt_paths != actual_paths:
        raise BundleContractError("sidecar artifact file set does not match receipt")


@contextmanager
def staged_sidecar(artifact_dir: Path, destination: Path) -> Iterator[None]:
    """Atomically replace ignored resources for one build, then restore the prior tree."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex
    staging = destination.parent / f".sidecar-stage-{suffix}"
    backup = destination.parent / f".sidecar-backup-{suffix}"
    shutil.copytree(artifact_dir, staging, symlinks=True)
    replaced = destination.exists()
    try:
        if replaced:
            os.replace(destination, backup)
        try:
            os.replace(staging, destination)
        except Exception:
            if replaced and backup.exists():
                os.replace(backup, destination)
            raise
        yield
    finally:
        if destination.exists():
            shutil.rmtree(destination)
        if replaced and backup.exists():
            os.replace(backup, destination)
        else:
            shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(staging, ignore_errors=True)


def _filtered_build_environment(source_sha: str) -> dict[str, str]:
    allowed = ("HOME", "LANG", "PATH", "TMPDIR", "USER")
    environment = {name: os.environ[name] for name in allowed if name in os.environ}
    environment["SAGE_BUILD_SHA"] = source_sha
    return environment


def _wait_for_disk_state(home: Path, timeout: float) -> tuple[Path, dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for path in home.rglob("desktop-host-state.json"):
            with suppress(OSError, json.JSONDecodeError):
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and isinstance(payload.get("orphan"), dict):
                    return path, payload
        time.sleep(0.1)
    raise BundleContractError("desktop host did not persist a ready sidecar identity")


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for_restarted_sidecar(
    state_path: Path,
    old_pid: int,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with suppress(OSError, json.JSONDecodeError, KeyError, TypeError):
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            record = cast(dict[str, Any], payload["orphan"])
            new_pid = int(record["pid"])
            if new_pid != old_pid and _process_exists(new_pid):
                return record
        time.sleep(0.1)
    raise BundleContractError("desktop host did not restart and revalidate the sidecar")


def _wait_for_webview_observations(state_path: Path, expected: int, timeout: float) -> None:
    path = state_path.parent / "diagnostics" / "desktop-sidecar.jsonl"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with suppress(OSError, json.JSONDecodeError):
            records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            observed = [
                record
                for record in records
                if isinstance(record, dict)
                and record.get("event") == "webview_capabilities_observed"
                and record.get("state") == "ready"
                and record.get("reason_code") == "desktop_session_authenticated"
                and set(record) == {"timestamp", "event", "state", "reason_code"}
            ]
            if len(observed) >= expected:
                return
        time.sleep(0.1)
    raise BundleContractError("WebView did not authenticate with the rotated sidecar session")


def _observed_process(pid: int) -> tuple[int, Path] | None:
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "lstart=", "-o", "comm="],
        capture_output=True,
        text=True,
    )
    fields = result.stdout.strip().split(maxsplit=5)
    if result.returncode != 0 or len(fields) != 6:
        return None
    started = datetime.strptime(" ".join(fields[:5]), "%a %b %d %H:%M:%S %Y")
    return int(started.astimezone().timestamp()), Path(fields[5]).resolve()


def _record_matches_process(record: Mapping[str, Any]) -> bool:
    try:
        pid = int(record["pid"])
        start_time = int(record["start_time"])
        executable = Path(str(record["executable"])).resolve()
    except (KeyError, TypeError, ValueError):
        return False
    observed = _observed_process(pid)
    return observed == (start_time, executable)


def _signal_verified(record: Mapping[str, Any], signal_number: signal.Signals) -> None:
    if not _record_matches_process(record):
        raise BundleContractError("desktop smoke process identity changed before signal")
    os.kill(int(record["pid"]), signal_number)


def _wait_for_no_processes(pids: set[int], timeout: float) -> None:
    deadline = time.monotonic() + timeout
    stable = 0
    while time.monotonic() < deadline:
        remaining = {pid for pid in pids if _process_exists(pid)}
        if not remaining:
            stable += 1
            if stable >= 3:
                return
        else:
            stable = 0
        time.sleep(0.1)
    raise BundleContractError(f"desktop smoke left processes: {sorted(remaining)}")


def smoke_bundled_app(app: Path, *, timeout: float = 30) -> dict[str, str]:
    """Launch the real app, force one sidecar restart, then request an explicit app quit."""
    executable = app / "Contents" / "MacOS" / "sage-desktop"
    if not executable.is_file():
        raise BundleContractError("bundled desktop executable is missing")
    if subprocess.run(["pgrep", "-x", "sage-desktop"], capture_output=True).returncode == 0:
        raise BundleContractError("close the running Sage app before bundle smoke")

    with tempfile.TemporaryDirectory(prefix="sage-desktop-home-") as temporary:
        home = Path(temporary)
        environment = {
            "HOME": str(home),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PATH": "/usr/bin:/bin",
            "TMPDIR": str(home),
        }
        process = subprocess.Popen(
            [str(executable)],
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        sidecar_records: dict[int, dict[str, Any]] = {}
        try:
            state_path, state = _wait_for_disk_state(home, timeout)
            _wait_for_webview_observations(state_path, 1, timeout)
            first_record = state["orphan"]
            first_pid = int(first_record["pid"])
            sidecar_records[first_pid] = first_record
            _signal_verified(first_record, signal.SIGKILL)
            restarted_record = _wait_for_restarted_sidecar(state_path, first_pid, timeout)
            restarted_pid = int(restarted_record["pid"])
            if not _record_matches_process(restarted_record):
                raise BundleContractError("restarted sidecar identity does not match runtime")
            sidecar_records[restarted_pid] = restarted_record
            _wait_for_webview_observations(state_path, 2, timeout)
            quit_result = subprocess.run(
                [
                    "/usr/bin/osascript",
                    "-e",
                    'tell application id "com.sage.learning" to quit',
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if quit_result.returncode != 0:
                raise BundleContractError("macOS did not accept the explicit Sage quit request")
            process.wait(timeout=timeout)
            _wait_for_no_processes(set(sidecar_records) | {process.pid}, timeout)
            final_state = json.loads(state_path.read_text(encoding="utf-8"))
            if final_state.get("orphan") is not None:
                raise BundleContractError("explicit exit did not clear the persisted orphan")
        finally:
            if process.poll() is None:
                process.terminate()
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=5)
            for record in sidecar_records.values():
                with suppress(ProcessLookupError, BundleContractError):
                    _signal_verified(record, signal.SIGTERM)
            with suppress(BundleContractError):
                _wait_for_no_processes(set(sidecar_records) | {process.pid}, 5)
    return {
        "app_launch": "passed",
        "crash_restart": "passed",
        "explicit_exit": "passed",
        "handshake_health": "passed",
        "process_cleanup": "passed",
        "webview_reconnect": "passed",
    }


def build(output_dir: Path) -> Path:
    """Build D0 from clean HEAD, stage it once, and verify the signed D1 app."""
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise BundleContractError("D1 bundle is limited to macOS arm64")
    if sys.version_info[:2] != (3, 12):
        raise BundleContractError("D1 bundle requires Python 3.12")
    root = Path(__file__).resolve().parents[1]
    output = output_dir.resolve()
    if output == root or root in output.parents:
        raise BundleContractError("desktop bundle output must be outside the repository")
    source_sha, dirty = _git_state(root)
    if dirty:
        raise BundleContractError("desktop bundle requires a clean HEAD")
    output.mkdir(parents=True, exist_ok=False)

    sidecar_receipt_path = sidecar_build.build(output / "sidecar-build")
    artifact_dir = sidecar_receipt_path.parent
    sidecar_receipt = json.loads(sidecar_receipt_path.read_text(encoding="utf-8"))
    lock_path = root / "desktop" / "sidecar" / "requirements-lock.txt"
    validate_sidecar_receipt(
        artifact_dir,
        sidecar_receipt,
        expected_sha=source_sha,
        expected_manifest=_expected_manifest(root),
        expected_lock_sha256=_sha256(lock_path),
    )

    frontend = root / "frontend"
    resource_dir = frontend / "src-tauri" / "binaries" / "sidecar"
    with staged_sidecar(artifact_dir, resource_dir):
        subprocess.run(
            [
                "npm",
                "run",
                "tauri",
                "--",
                "build",
                "--bundles",
                "app",
                "--target",
                TARGET,
            ],
            cwd=frontend,
            env=_filtered_build_environment(source_sha),
            check=True,
        )

    built_app = frontend / "src-tauri" / "target" / TARGET / "release" / "bundle" / "macos" / "Sage.app"
    app = output / "Sage.app"
    shutil.copytree(built_app, app, symlinks=True)
    bundled_receipt = app / "Contents" / "Resources" / "sidecar" / sidecar_build.RECEIPT_NAME
    if bundled_receipt.read_bytes() != sidecar_receipt_path.read_bytes():
        raise BundleContractError("bundled sidecar receipt differs from the fresh D0 build")
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True)
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    validate_sidecar_receipt(
        bundled_receipt.parent,
        json.loads(bundled_receipt.read_text(encoding="utf-8")),
        expected_sha=source_sha,
        expected_manifest=_expected_manifest(root),
        expected_lock_sha256=_sha256(lock_path),
    )
    smoke = smoke_bundled_app(app)
    receipt = {
        "schema_version": 1,
        "source_sha": source_sha,
        "source_dirty": False,
        "target": TARGET,
        "signature": "ad-hoc",
        "sidecar_receipt_sha256": _sha256(bundled_receipt),
        "sidecar": sidecar_receipt,
        "checks": smoke,
    }
    receipt_path = output / "desktop-bundle-receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    receipt = build(_parser().parse_args(argv).output_dir)
    print(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
