"""Dependency-firewall tests for the reusable harness package."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "packages" / "sage_harness" / "sage_harness"
FORBIDDEN_ROOTS = {"agents", "api", "core", "db", "frontend", "mcp_servers", "models"}


def _absolute_import_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def test_harness_package_does_not_import_application_modules() -> None:
    """Reusable code must depend on ports rather than Sage concrete modules."""
    violations: dict[str, list[str]] = {}
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        forbidden = sorted(_absolute_import_roots(path) & FORBIDDEN_ROOTS)
        if forbidden:
            violations[str(path.relative_to(PACKAGE_ROOT))] = forbidden

    assert violations == {}
