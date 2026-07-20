from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEV_SCRIPT = ROOT / "scripts" / "dev.sh"
CHECK_SCRIPT = ROOT / "scripts" / "check.sh"


def run_check(env_file: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "SAGE_ENV_FILE": str(env_file),
            "SAGE_DEV_CHECK_ONLY": "1",
            "SAGE_PYTHON": sys.executable,
        }
    )
    return subprocess.run(
        ["bash", str(DEV_SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )


def test_dev_script_accepts_external_env_without_printing_secret(tmp_path: Path) -> None:
    secret = "secret-must-not-appear"
    env_file = tmp_path / "sage.env"
    env_file.write_text(f"DEEPSEEK_API_KEY={secret}\n", encoding="utf-8")

    result = run_check(env_file)

    assert result.returncode == 0
    assert "Configured model providers: DEEPSEEK" in result.stdout
    assert "Local development configuration is valid." in result.stdout
    assert secret not in result.stdout
    assert secret not in result.stderr


def test_dev_script_reports_missing_external_env(tmp_path: Path) -> None:
    env_file = tmp_path / "missing.env"

    result = run_check(env_file)

    assert result.returncode == 1
    assert f"Missing environment file: {env_file}" in result.stdout


def test_dev_script_rejects_missing_project_interpreter(tmp_path: Path) -> None:
    env_file = tmp_path / "sage.env"
    env_file.write_text("DEEPSEEK_API_KEY=test-only\n", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "SAGE_ENV_FILE": str(env_file),
            "SAGE_DEV_CHECK_ONLY": "1",
            "SAGE_PYTHON": str(tmp_path / "missing-python"),
        }
    )

    result = subprocess.run(
        ["bash", str(DEV_SCRIPT)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 1
    assert "Sage Python interpreter not found" in result.stdout
    assert "bash scripts/bootstrap-dev-env.sh" in result.stdout


def test_check_script_uses_the_repository_interpreter() -> None:
    script = CHECK_SCRIPT.read_text(encoding="utf-8")

    assert 'PYTHON_BIN="${SAGE_PYTHON:-${ROOT_DIR}/.venv/bin/python}"' in script
    assert '"${PYTHON_BIN}" -m ruff check' in script
    assert '"${PYTHON_BIN}" -m mypy' in script
    assert '"${PYTHON_BIN}" -m pytest' in script


def test_bootstrap_repairs_macos_hidden_flags_for_editable_packages() -> None:
    script = (ROOT / "scripts" / "bootstrap-dev-env.sh").read_text(encoding="utf-8")

    assert 'if [[ "$(uname -s)" == "Darwin" ]]' in script
    assert 'chflags -R nohidden "${VENV_DIR}"' in script
