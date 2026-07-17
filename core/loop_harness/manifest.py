"""Hash-pinned policy/controller manifest stored outside the repository."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from core.loop_harness import POLICY_VERSION
from core.loop_harness.errors import LoopBlockedError

_FIXED_FILES = (
    "AGENTS.md",
    "scripts/loopctl.py",
    "scripts/install_loop_harness.sh",
)
_MANAGED_DIRECTORIES = (
    "core/loop_harness",
    "tests/core/loop_harness",
    "docs/loop-harness",
    ".github/workflows",
)


def write_manifest(controller_root: Path, destination: Path) -> dict[str, Any]:
    manifest = build_manifest(controller_root)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists() and destination.is_symlink():
        raise ValueError("manifest must not be a symlink")
    payload = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=".manifest-", dir=destination.parent, text=True)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    return manifest


def build_manifest(controller_root: Path) -> dict[str, Any]:
    files = _managed_files(controller_root)
    if not files:
        raise ValueError("no managed Loop Harness files found")
    hashes: dict[str, str] = {}
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"managed file must be a regular file: {path}")
        relative = path.relative_to(controller_root).as_posix()
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "policy_version": POLICY_VERSION,
        "controller_commit": _head_sha(controller_root),
        "files": hashes,
    }


def validate_manifest(controller_root: Path, source: Path) -> dict[str, Any]:
    if not source.is_file() or source.is_symlink():
        raise LoopBlockedError("PAUSED_POLICY_DRIFT", "controlled manifest is missing")
    try:
        expected = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LoopBlockedError("PAUSED_POLICY_DRIFT", "controlled manifest is invalid") from exc
    actual = build_manifest(controller_root)
    if expected != actual:
        raise LoopBlockedError(
            "PAUSED_POLICY_DRIFT", "policy or controller files changed after installation"
        )
    return actual


def _managed_files(root: Path) -> list[Path]:
    files: set[Path] = set()
    for relative in _FIXED_FILES:
        candidate = root / relative
        if candidate.exists():
            files.add(candidate)
    for relative in _MANAGED_DIRECTORIES:
        directory = root / relative
        if directory.is_dir():
            files.update(
                path
                for path in directory.rglob("*")
                if path.is_file() and _is_managed_source(path, directory)
            )
    return sorted(files)


def _is_managed_source(path: Path, managed_root: Path) -> bool:
    relative = path.relative_to(managed_root)
    if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
        return False
    if managed_root.name == "workflows":
        return path.suffix in {".yml", ".yaml"}
    if managed_root.name == "loop-harness" and "docs" in managed_root.parts:
        return path.suffix == ".md"
    return path.suffix in {".py", ".json", ".md"}


def _head_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise ValueError("controller root is not a Git checkout")
    return result.stdout.strip()
