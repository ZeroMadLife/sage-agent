"""Workspace context and path-safety helpers for the coding agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_TOOL_OUTPUT = 4000
IGNORED_PATH_NAMES = {
    ".git",
    ".coding",
    ".pico",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    "node_modules",
}


def now() -> str:
    """Return an ISO UTC timestamp."""
    return datetime.now(UTC).isoformat()


def clip(text: Any, limit: int = MAX_TOOL_OUTPUT) -> str:
    """Clip long text while preserving a visible truncation marker."""
    value = str(text)
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"


@dataclass
class WorkspaceContext:
    """A path-safe working directory for coding tools."""

    root: Path
    _read_fingerprints: dict[str, tuple[bool, int, int]] = field(default_factory=dict)
    _self_authored_fingerprints: dict[str, tuple[bool, int, int]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()

    def path(self, raw_path: str | Path) -> Path:
        """Resolve a user path and reject escapes outside the workspace root."""
        raw = Path(raw_path)
        candidate = raw if raw.is_absolute() else Path(self.root) / raw
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path escapes workspace root: {raw_path}") from exc
        return resolved

    def relative(self, path: str | Path) -> str:
        """Return a workspace-relative path string."""
        return str(self.path(path).relative_to(self.root))

    def mark_read(self, raw_path: str | Path) -> None:
        """Record the current freshness of a file read by the agent."""
        path = self.path(raw_path)
        self._read_fingerprints[str(path)] = self._fingerprint(path)

    def mark_self_authored(self, raw_path: str | Path) -> None:
        """Record the current freshness of a file written by the agent."""
        path = self.path(raw_path)
        self._self_authored_fingerprints[str(path)] = self._fingerprint(path)

    def has_fresh_read(self, raw_path: str | Path) -> bool:
        """Return whether the file was read and has not changed since."""
        path = self.path(raw_path)
        current = self._fingerprint(path)
        key = str(path)
        return self._read_fingerprints.get(key) == current

    def has_self_authored_freshness(self, raw_path: str | Path) -> bool:
        """Return whether the file was authored by the agent and is unchanged."""
        path = self.path(raw_path)
        current = self._fingerprint(path)
        return self._self_authored_fingerprints.get(str(path)) == current

    @staticmethod
    def _fingerprint(path: Path) -> tuple[bool, int, int]:
        """Return a simple file freshness tuple."""
        if not path.exists():
            return (False, 0, 0)
        stat = path.stat()
        return (True, stat.st_mtime_ns, stat.st_size)
