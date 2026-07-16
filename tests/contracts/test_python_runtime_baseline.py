"""Release contract for the Sage Python runtime baseline."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _requirements() -> dict[str, str]:
    requirements: dict[str, str] = {}
    for raw_line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        requirements[name.lower()] = version
    return requirements


def test_python_and_agent_dependencies_match_the_harness_baseline() -> None:
    """The release install uses the audited Python and LangChain generation."""
    requirements = _requirements()

    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"
    assert requirements["langchain"] == "1.2.15"
    assert requirements["langchain-core"] == "1.4.9"
    assert requirements["langgraph"] == "1.1.9"
    assert requirements["langchain-mcp-adapters"] == "0.2.2"
    assert requirements["greenlet"] == "3.5.3"

    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert config["tool"]["mypy"]["python_version"] == "3.12"


def test_release_install_excludes_abandoned_mem0_stack() -> None:
    """The unused travel demo must not pull Mem0, Qdrant, or Torch into Sage."""
    requirements = _requirements()

    assert "mem0ai" not in requirements
    assert "qdrant-client" not in requirements
    assert "sentence-transformers" not in requirements
    assert "langgraph-supervisor" not in requirements
    assert "langchain-community" not in requirements

    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "qdrant:" not in compose
    assert "qdrant_data" not in compose
