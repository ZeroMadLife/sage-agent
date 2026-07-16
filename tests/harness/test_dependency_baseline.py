"""Packaging contract for the independently installable harness."""

from __future__ import annotations

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
    assert project["dependencies"] == []
    assert version("sage-harness") == project["version"]
    assert sage_harness.__name__ == "sage_harness"


def test_release_install_includes_the_editable_harness_package() -> None:
    """The root release install must make the package available to Sage."""
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    normalized = {line.strip() for line in requirements if line.strip()}

    assert "-e ./packages/sage_harness" in normalized
