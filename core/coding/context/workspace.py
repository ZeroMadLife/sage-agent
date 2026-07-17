"""Workspace context and path-safety helpers for the coding agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_TOOL_OUTPUT = 4000
HIDDEN_CONTROL_DIR_NAMES = frozenset(
    {
        ".aws",
        ".cc-connect",
        ".claude",
        ".coding",
        ".codex",
        ".git",
        ".gnupg",
        ".idea",
        ".local",
        ".pico",
        ".playwright-cli",
        ".sage",
        ".ssh",
        ".superpowers",
        ".ustht",
        ".vscode",
        ".zcode",
    }
)
IGNORED_PATH_NAMES = {
    *HIDDEN_CONTROL_DIR_NAMES,
    "__pycache__",
    ".DS_Store",
    ".coverage",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    "node_modules",
}
PROTECTED_PATH_NAMES = frozenset(
    {
        *HIDDEN_CONTROL_DIR_NAMES,
        ".netrc",
        ".npmrc",
        ".pypirc",
        "credentials",
        "credentials.json",
        "id_ed25519",
        "id_rsa",
    }
)
_SAFE_ENV_SUFFIXES = (".example", ".sample", ".template")


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
        """Resolve a user path and reject escapes or protected credentials."""
        resolved = self.internal_path(raw_path)
        relative = resolved.relative_to(self.root)
        if self.is_protected(relative):
            raise ValueError(f"path is protected by workspace policy: {raw_path}")
        return resolved

    def internal_path(self, raw_path: str | Path) -> Path:
        """Resolve a trusted Sage-owned path while still rejecting escapes."""
        raw = Path(raw_path)
        candidate = raw if raw.is_absolute() else Path(self.root) / raw
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"path escapes workspace root: {raw_path}") from exc
        return resolved

    def is_protected(self, raw_path: str | Path) -> bool:
        """Return whether a workspace-relative path may contain credentials."""
        raw = Path(raw_path)
        if raw.is_absolute():
            try:
                raw = raw.resolve().relative_to(self.root)
            except ValueError:
                return True
        parts = tuple(part.lower() for part in raw.parts if part not in {"", "."})
        if not parts:
            return False
        if any(part in PROTECTED_PATH_NAMES for part in parts):
            return True
        name = parts[-1]
        return name.startswith(".env") and not name.endswith(_SAFE_ENV_SUFFIXES)

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
