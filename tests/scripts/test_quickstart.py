from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
QUICKSTART = ROOT / "scripts" / "quickstart.sh"


@pytest.fixture()
def frontend_dependencies() -> None:
    """Avoid a network install while exercising the shell contract."""
    dependency_dir = ROOT / "frontend" / "node_modules"
    existed = dependency_dir.exists()
    dependency_dir.mkdir(exist_ok=True)
    try:
        yield
    finally:
        if not existed:
            shutil.rmtree(dependency_dir, ignore_errors=True)


def run_quickstart(env_file: Path, *, mode: str = "--check") -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "SAGE_ENV_FILE": str(env_file),
            "SAGE_PYTHON": sys.executable,
        }
    )
    return subprocess.run(
        ["bash", str(QUICKSTART), mode],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )


def test_quickstart_creates_private_env_and_runs_checks(
    tmp_path: Path, frontend_dependencies: None
) -> None:
    env_file = tmp_path / "sage.env"

    result = run_quickstart(env_file)

    assert result.returncode == 0
    assert env_file.is_file()
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert "Sage local product checks passed." in result.stdout


def test_quickstart_does_not_overwrite_existing_env(
    tmp_path: Path, frontend_dependencies: None
) -> None:
    env_file = tmp_path / "sage.env"
    original = "DEEPSEEK_API_KEY=test-only\n"
    env_file.write_text(original, encoding="utf-8")
    env_file.chmod(0o600)

    result = run_quickstart(env_file)

    assert result.returncode == 0
    assert env_file.read_text(encoding="utf-8") == original


def test_quickstart_never_prints_provider_secret(
    tmp_path: Path, frontend_dependencies: None
) -> None:
    secret = "quickstart-secret-must-not-appear"
    env_file = tmp_path / "sage.env"
    env_file.write_text(f"DEEPSEEK_API_KEY={secret}\n", encoding="utf-8")
    env_file.chmod(0o600)

    result = run_quickstart(env_file)

    assert result.returncode == 0
    assert secret not in result.stdout
    assert secret not in result.stderr


def test_quickstart_rejects_symbolic_link_env(tmp_path: Path) -> None:
    target = tmp_path / "target.env"
    target.write_text("DEEPSEEK_API_KEY=test-only\n", encoding="utf-8")
    env_file = tmp_path / "sage.env"
    env_file.symlink_to(target)

    result = run_quickstart(env_file)

    assert result.returncode == 1
    assert "cannot be a symbolic link" in result.stderr


def test_quickstart_reports_incomplete_explicit_python(tmp_path: Path) -> None:
    env_file = tmp_path / "sage.env"
    env_file.write_text("DEEPSEEK_API_KEY=test-only\n", encoding="utf-8")
    env_file.chmod(0o600)
    env = os.environ.copy()
    env.update(
        {
            "SAGE_ENV_FILE": str(env_file),
            "SAGE_PYTHON": str(tmp_path / "missing-python"),
        }
    )

    result = subprocess.run(
        ["bash", str(QUICKSTART), "--check"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert "SAGE_PYTHON is missing Sage dependencies" in result.stderr


def test_quickstart_static_contract_keeps_dev_script_as_launcher() -> None:
    script = QUICKSTART.read_text(encoding="utf-8")

    assert "scripts/bootstrap-dev-env.sh" in script
    assert "scripts/dev.sh" in script
    assert "SAGE_DEV_CHECK_ONLY=1" in script
    assert "SAGE_DEV_RELOAD=0" in script
    assert "exec env" in script
