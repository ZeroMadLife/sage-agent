"""Build receipt and artifact hygiene contracts."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from desktop.sidecar.build import ArtifactHygieneError, build_receipt, verify_artifact_hygiene

ROOT = Path(__file__).resolve().parents[2]


def _pinned_requirements(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if "==" in line and not line.startswith("#"):
            name, version = line.split("==", 1)
            pins[name.split("[", 1)[0].lower()] = version
    return pins


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
            "orjson",
            "psycopg2-binary",
            "pydantic",
            "tenacity",
            "uvicorn",
        )
    }
    assert lock_pins.items() >= desktop_pins.items()
    assert lock_pins["pyinstaller"] == "6.16.0"
    assert harness["project"]["requires-python"] == ">=3.12"


def test_build_receipt_is_relative_deterministic_and_secret_free(tmp_path: Path) -> None:
    artifact = tmp_path / "sage-api-aarch64-apple-darwin"
    artifact.mkdir()
    (artifact / "sage-api-aarch64-apple-darwin").write_bytes(b"sidecar")

    first = build_receipt(
        artifact_dir=artifact,
        source_sha="abc123",
        source_dirty=False,
        pyinstaller_version="6.16.0",
        smoke={"live": "passed", "ready": "passed"},
    )
    second = build_receipt(
        artifact_dir=artifact,
        source_sha="abc123",
        source_dirty=False,
        pyinstaller_version="6.16.0",
        smoke={"live": "passed", "ready": "passed"},
    )

    assert first == second
    assert first["schema_version"] == 1
    assert first["source_sha"] == "abc123"
    assert first["source_dirty"] is False
    assert len(first["dependency_lock_sha256"]) == 64
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
