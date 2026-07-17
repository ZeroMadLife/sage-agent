from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEV_SCRIPT = ROOT / "scripts" / "dev.sh"


def run_check(env_file: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "SAGE_ENV_FILE": str(env_file),
            "SAGE_DEV_CHECK_ONLY": "1",
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
