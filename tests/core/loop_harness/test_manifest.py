from __future__ import annotations

import subprocess

import pytest

from core.loop_harness.errors import LoopBlockedError
from core.loop_harness.manifest import validate_manifest, write_manifest


def _git(root, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _controller_repo(tmp_path):
    root = tmp_path / "controller"
    (root / "docs/loop-harness").mkdir(parents=True)
    (root / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    (root / "docs/loop-harness/POLICY.md").write_text("policy\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.name", "Loop Test")
    _git(root, "config", "user.email", "loop@example.com")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "baseline")
    return root


def test_manifest_detects_policy_drift(tmp_path) -> None:
    root = _controller_repo(tmp_path)
    manifest = tmp_path / "state/manifest.json"
    write_manifest(root, manifest)

    validate_manifest(root, manifest)
    (root / "docs/loop-harness/POLICY.md").write_text("changed\n", encoding="utf-8")

    with pytest.raises(LoopBlockedError, match="changed after installation") as exc:
        validate_manifest(root, manifest)
    assert exc.value.code == "PAUSED_POLICY_DRIFT"


def test_manifest_rejects_symlinked_managed_file(tmp_path) -> None:
    root = _controller_repo(tmp_path)
    policy = root / "docs/loop-harness/POLICY.md"
    policy.unlink()
    policy.symlink_to(root / "AGENTS.md")

    with pytest.raises(ValueError, match="regular file"):
        write_manifest(root, tmp_path / "manifest.json")


def test_manifest_ignores_python_bytecode(tmp_path) -> None:
    root = _controller_repo(tmp_path)
    manifest = tmp_path / "state/manifest.json"
    write_manifest(root, manifest)
    cache = root / "tests/core/loop_harness/__pycache__"
    cache.mkdir(parents=True)
    (cache / "test_state.cpython-311.pyc").write_bytes(b"generated")

    validate_manifest(root, manifest)
