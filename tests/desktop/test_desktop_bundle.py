"""Contracts for the single D1 desktop bundle entry."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from desktop.bundle import (
    BundleContractError,
    _request_pid_quit,
    staged_sidecar,
    validate_sidecar_receipt,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _receipt(artifact: Path) -> dict[str, object]:
    payload = artifact / "sage-api-aarch64-apple-darwin"
    payload.write_bytes(b"fresh-sidecar")
    receipt = {
        "schema_version": 1,
        "source_sha": "a" * 40,
        "source_dirty": False,
        "target": "aarch64-apple-darwin",
        "dependency_lock_sha256": "b" * 64,
        "build_environment": {
            "lock_sha256": "b" * 64,
            "manifest": {"fastapi": "1.0", "sage-harness": "0.1.0"},
            "harness": {
                "name": "sage-harness",
                "version": "0.1.0",
                "wheel": "sage_harness-0.1.0-py3-none-any.whl",
                "wheel_sha256": "c" * 64,
                "source_sha256": "d" * 64,
            },
        },
        "artifact": {
            "name": "sage-api-aarch64-apple-darwin",
            "files": [
                {
                    "path": payload.name,
                    "size": payload.stat().st_size,
                    "sha256": _sha256(payload),
                }
            ],
        },
        "smoke": {
            name: "passed"
            for name in (
                "child_process_cleanup",
                "core_imports",
                "graceful_exit",
                "liveness",
                "random_loopback",
                "readiness",
                "sqlite_checkpoint_reopen",
                "tls_client",
            )
        },
    }
    (artifact / "build-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    return receipt


def test_receipt_validation_requires_clean_head_manifest_hashes_and_all_smoke(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    receipt = _receipt(artifact)

    validate_sidecar_receipt(
        artifact,
        receipt,
        expected_sha="a" * 40,
        expected_manifest={"fastapi": "1.0", "sage-harness": "0.1.0"},
        expected_lock_sha256="b" * 64,
    )

    mutations = [
        ("source_sha", "f" * 40),
        ("source_dirty", True),
        ("target", "x86_64-apple-darwin"),
        ("dependency_lock_sha256", "e" * 64),
    ]
    for field, value in mutations:
        invalid = {**receipt, field: value}
        with pytest.raises(BundleContractError):
            validate_sidecar_receipt(
                artifact,
                invalid,
                expected_sha="a" * 40,
                expected_manifest={"fastapi": "1.0", "sage-harness": "0.1.0"},
                expected_lock_sha256="b" * 64,
            )

    missing_smoke = json.loads(json.dumps(receipt))
    del missing_smoke["smoke"]["liveness"]
    with pytest.raises(BundleContractError):
        validate_sidecar_receipt(
            artifact,
            missing_smoke,
            expected_sha="a" * 40,
            expected_manifest={"fastapi": "1.0", "sage-harness": "0.1.0"},
            expected_lock_sha256="b" * 64,
        )

    unsafe_path = json.loads(json.dumps(receipt))
    unsafe_path["artifact"]["files"][0]["path"] = "../outside"
    with pytest.raises(BundleContractError):
        validate_sidecar_receipt(
            artifact,
            unsafe_path,
            expected_sha="a" * 40,
            expected_manifest={"fastapi": "1.0", "sage-harness": "0.1.0"},
            expected_lock_sha256="b" * 64,
        )


def test_atomic_staging_never_reuses_the_ignored_sidecar(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    _receipt(artifact)
    destination = tmp_path / "binaries" / "sidecar"
    destination.mkdir(parents=True)
    (destination / "stale-marker").write_text("must disappear", encoding="utf-8")

    with staged_sidecar(artifact, destination):
        assert not (destination / "stale-marker").exists()
        assert (destination / "sage-api-aarch64-apple-darwin").read_bytes() == b"fresh-sidecar"

    assert (destination / "stale-marker").read_text(encoding="utf-8") == "must disappear"
    assert not list(destination.parent.glob(".sidecar-stage-*"))
    assert not list(destination.parent.glob(".sidecar-backup-*"))


def test_explicit_quit_targets_the_verified_pid_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def successful_quit(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="0 0 0\n", stderr="")

    monkeypatch.setattr("desktop.bundle.subprocess.run", successful_quit)
    _request_pid_quit(4242)

    assert commands[0][0] == "/usr/bin/swift"
    assert commands[0][-1] == "4242"
    assert "typeKernelProcessID" in commands[0][2]
    assert "kAEQuitApplication" in commands[0][2]

    def rejected_quit(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout="0 0 -600\n", stderr="")

    monkeypatch.setattr("desktop.bundle.subprocess.run", rejected_quit)
    with pytest.raises(BundleContractError, match="explicit Sage quit"):
        _request_pid_quit(4242)
