"""Packaging contract for the independently installable harness."""

from __future__ import annotations

import json
import tomllib
from importlib.metadata import version
from pathlib import Path

import sage_harness

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "packages" / "sage_harness"


def test_harness_is_an_independent_python_312_package() -> None:
    """The reusable package owns its metadata and has no application dependency."""
    metadata = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]

    assert project["name"] == "sage-harness"
    assert project["requires-python"] == ">=3.12"
    assert project["dependencies"] == [
        "langchain==1.2.15",
        "langchain-core==1.4.9",
        "langgraph==1.1.9",
        "langgraph-checkpoint-sqlite==3.1.0",
    ]
    assert version("sage-harness") == project["version"]
    assert sage_harness.__name__ == "sage_harness"


def test_release_install_includes_the_editable_harness_package() -> None:
    """The root release install must make the package available to Sage."""
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    normalized = {line.strip() for line in requirements if line.strip()}

    assert "-e ./packages/sage_harness" in normalized


def test_local_ide_contract_uses_the_repository_python_312_environment() -> None:
    """Checked-in IDE entry points must not fall back to a stale system Python."""
    python_version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    vscode_settings = json.loads((ROOT / ".vscode" / "settings.json").read_text(encoding="utf-8"))
    vscode_launch = json.loads((ROOT / ".vscode" / "launch.json").read_text(encoding="utf-8"))
    vscode_tasks = json.loads((ROOT / ".vscode" / "tasks.json").read_text(encoding="utf-8"))

    assert python_version == "3.12"
    assert vscode_settings["python.defaultInterpreterPath"] == "${workspaceFolder}/.venv/bin/python"
    assert vscode_launch["configurations"][0]["python"] == "${workspaceFolder}/.venv/bin/python"
    backend_task = next(task for task in vscode_tasks["tasks"] if task["label"] == "dev: backend")
    assert backend_task["command"].startswith(".venv/bin/python -m uvicorn")
