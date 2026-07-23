"""Bounded workspace diff tracking for coding runs."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Patterns to ignore (never diff these)
IGNORED_DIRS = {
    ".git",
    ".coding",
    ".cache",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".next",
}
IGNORED_FILE_PATTERNS = (".env", ".DS_Store", ".pyc", ".so", ".dylib", ".lock")
SAGE_RUNTIME_FILES = {
    "usage.sqlite3",
    "usage.sqlite3-shm",
    "usage.sqlite3-wal",
}
MAX_FILE_SIZE = 256 * 1024  # 256KB - files larger than this are marked "truncated"
MAX_DIFF_FILES = 50  # Maximum number of changed files to track
MAX_SNAPSHOT_FILES = 20_000
GIT_SCAN_TIMEOUT_SECONDS = 5.0
# Small text files (<= this many bytes) have their before-content stored so a
# real unified diff can be generated after the run.
STORE_CONTENT_LIMIT = 16 * 1024  # 16KB


@dataclass
class FileSnapshot:
    """Content hash + metadata for one file."""

    path: str
    hash: str = ""
    size: int = 0
    is_text: bool = True
    exists: bool = True
    content: str = ""


@dataclass
class FileChange:
    """One changed file between before/after snapshots."""

    path: str
    status: str  # "added", "modified", "deleted"
    before_hash: str = ""
    after_hash: str = ""
    diff: str = ""
    truncated: bool = False
    binary: bool = False
    ignored_sensitive: bool = False


@dataclass
class WorkspaceDiff:
    """Bounded diff artifact for one run."""

    run_id: str
    changed_files: list[FileChange] = field(default_factory=list)
    file_count: int = 0
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "changed_files": [
                {
                    "path": f.path,
                    "status": f.status,
                    "before_hash": f.before_hash,
                    "after_hash": f.after_hash,
                    "diff": f.diff,
                    "truncated": f.truncated,
                    "binary": f.binary,
                    "ignored_sensitive": f.ignored_sensitive,
                }
                for f in self.changed_files
            ],
            "file_count": self.file_count,
            "truncated": self.truncated,
        }


class WorkspaceDiffTracker:
    """Track workspace file changes across a run lifecycle."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self._before: dict[str, FileSnapshot] = {}
        self._before_truncated = False
        self._baseline_ready = False
        self._baseline_run_id = ""

    def snapshot_before_run(self, run_id: str = "") -> None:
        """Capture workspace state before the run starts."""
        self._before, self._before_truncated = self._scan_workspace()
        self._baseline_ready = True
        self._baseline_run_id = run_id

    def snapshot_after_run(self, run_id: str) -> WorkspaceDiff:
        """Compare current state with before snapshot, return diff artifact."""
        if not self._baseline_ready:
            raise RuntimeError("workspace diff baseline is unavailable")
        if self._baseline_run_id and self._baseline_run_id != run_id:
            raise RuntimeError("workspace diff baseline belongs to another run")
        after, after_truncated = self._scan_workspace()
        changes: list[FileChange] = []

        all_paths = set(self._before.keys()) | set(after.keys())
        # Count total changed files (independent of the MAX_DIFF_FILES cap) so
        # `truncated` reflects changed-file count, not total workspace size.
        total_changed = 0
        for path in sorted(all_paths):
            before = self._before.get(path)
            after_snap = after.get(path)
            if before and after_snap and before.hash == after_snap.hash:
                continue  # unchanged
            total_changed += 1

        for path in sorted(all_paths):
            before = self._before.get(path)
            after_snap = after.get(path)

            if before and after_snap and before.hash == after_snap.hash:
                continue  # unchanged

            if before and not after_snap:
                # deleted
                changes.append(
                    FileChange(
                        path=path,
                        status="deleted",
                        before_hash=before.hash,
                        after_hash="",
                    )
                )
            elif not before and after_snap:
                # added
                change = FileChange(
                    path=path,
                    status="added",
                    before_hash="",
                    after_hash=after_snap.hash,
                )
                if not after_snap.is_text:
                    change.binary = True
                else:
                    change.diff = self._generate_added_diff(path, after_snap)
                changes.append(change)
            elif before and after_snap and before.hash != after_snap.hash:
                # modified
                change = FileChange(
                    path=path,
                    status="modified",
                    before_hash=before.hash,
                    after_hash=after_snap.hash,
                )
                if not before.is_text or not after_snap.is_text:
                    change.binary = True
                else:
                    change.diff, change.truncated = self._generate_diff(path, before, after_snap)
                changes.append(change)

            if len(changes) >= MAX_DIFF_FILES:
                break

        truncated = self._before_truncated or after_truncated or total_changed > MAX_DIFF_FILES
        try:
            return WorkspaceDiff(
                run_id=run_id,
                changed_files=changes,
                file_count=len(changes),
                truncated=truncated,
            )
        finally:
            self.clear_baseline()

    def clear_baseline(self) -> None:
        """Release per-run snapshot memory after completion or cancellation."""
        self._before = {}
        self._before_truncated = False
        self._baseline_ready = False
        self._baseline_run_id = ""

    def write_artifact(self, diff: WorkspaceDiff, evidence_root: Path) -> Path:
        """Write diff artifact to evidence directory."""
        diff_dir = evidence_root / diff.run_id
        diff_dir.mkdir(parents=True, exist_ok=True)
        diff_path = diff_dir / "diff.json"
        diff_path.write_text(
            json.dumps(diff.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return diff_path

    def _scan_workspace(self) -> tuple[dict[str, FileSnapshot], bool]:
        """Scan workspace and return path -> snapshot mapping."""
        snapshots: dict[str, FileSnapshot] = {}
        candidates = self._git_candidate_paths()
        if candidates is None:
            candidates, truncated = self._walk_candidate_paths()
        else:
            truncated = len(candidates) > MAX_SNAPSHOT_FILES
            candidates = candidates[:MAX_SNAPSHOT_FILES]
        for rel_path in candidates:
            fpath = self.workspace_root / rel_path
            if fpath.is_symlink() or not fpath.is_file():
                continue
            try:
                stat = fpath.stat()
            except OSError:
                continue
            snap = FileSnapshot(path=rel_path, size=stat.st_size)
            try:
                digest, content_bytes = self._hash_file(
                    fpath,
                    capture=stat.st_size <= MAX_FILE_SIZE,
                )
                snap.hash = digest
                snap.is_text = self._is_text(content_bytes) and stat.st_size <= MAX_FILE_SIZE
                if snap.is_text and stat.st_size <= STORE_CONTENT_LIMIT:
                    snap.content = content_bytes.decode("utf-8", errors="replace")
            except (OSError, PermissionError):
                snap.exists = False
            snapshots[rel_path] = snap
        return snapshots, truncated

    def _git_candidate_paths(self) -> list[str] | None:
        """Use Git's own tracked/untracked view instead of walking ignored trees."""
        try:
            completed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.workspace_root),
                    "ls-files",
                    "-z",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "--",
                    ".",
                ],
                check=False,
                capture_output=True,
                timeout=GIT_SCAN_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        paths = {os.fsdecode(raw) for raw in completed.stdout.split(b"\0") if raw}
        return sorted(
            path
            for path in paths
            if self._valid_candidate(path) and not self._is_ignored_path(path)
        )

    def _walk_candidate_paths(self) -> tuple[list[str], bool]:
        candidates: list[str] = []
        for root, dirs, files in os.walk(self.workspace_root):
            dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
            for fname in sorted(files):
                rel_path = str((Path(root) / fname).relative_to(self.workspace_root))
                if self._is_ignored_path(rel_path):
                    continue
                if len(candidates) >= MAX_SNAPSHOT_FILES:
                    return candidates, True
                candidates.append(rel_path)
        return candidates, False

    @staticmethod
    def _valid_candidate(value: str) -> bool:
        path = Path(value)
        return bool(value) and not path.is_absolute() and ".." not in path.parts

    @staticmethod
    def _is_ignored_path(rel_path: str) -> bool:
        path = Path(rel_path)
        if any(part in IGNORED_DIRS for part in path.parts[:-1]):
            return True
        if any(
            path.name.endswith(pattern) or path.name.startswith(pattern)
            for pattern in IGNORED_FILE_PATTERNS
        ):
            return True
        return WorkspaceDiffTracker._is_runtime_path(rel_path)

    @staticmethod
    def _hash_file(path: Path, *, capture: bool) -> tuple[str, bytes]:
        digest = hashlib.sha256()
        captured = bytearray()
        with path.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                digest.update(chunk)
                if capture:
                    captured.extend(chunk)
        return digest.hexdigest(), bytes(captured)

    @staticmethod
    def _is_runtime_path(rel_path: str) -> bool:
        """Exclude Sage's mutable usage database without hiding user `.sage` config."""
        path = Path(rel_path)
        return len(path.parts) == 2 and path.parts[0] == ".sage" and path.name in SAGE_RUNTIME_FILES

    @staticmethod
    def _is_text(content: bytes) -> bool:
        """Heuristic: check if content is text (no null bytes in first 8KB)."""
        return b"\x00" not in content[:8192]

    def _generate_diff(
        self, rel_path: str, before: FileSnapshot, after: FileSnapshot
    ) -> tuple[str, bool]:
        """Return (diff_text, truncated) for a modified text file.

        - If neither side stored its content (both too large), return a short
          summary referencing the hashes instead of a full-file diff.
        - Otherwise generate a real unified diff, reading the after content
          from disk if it wasn't stored, and truncate the result if it exceeds
          MAX_FILE_SIZE.
        """
        if not before.content and not after.content:
            # Both too large to store content; a full diff would be misleading.
            return (
                f"File modified (hash: {before.hash[:8]} -> {after.hash[:8]})",
                False,
            )

        after_text = after.content
        if not after_text:
            # After content wasn't stored (too large); read it now if possible.
            fpath = self.workspace_root / rel_path
            try:
                after_text = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ("", False)
        before_lines = before.content.splitlines(keepends=True) if before.content else []
        after_lines = after_text.splitlines(keepends=True)
        diff = difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
        )
        result = "".join(diff)
        if len(result) > MAX_FILE_SIZE:
            return (result[:MAX_FILE_SIZE], True)
        return (result, False)

    def _generate_added_diff(self, rel_path: str, after: FileSnapshot) -> str:
        """Generate unified diff for a newly added text file."""
        after_text = after.content
        if not after_text:
            fpath = self.workspace_root / rel_path
            try:
                after_text = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
        after_lines = after_text.splitlines(keepends=True)
        diff = difflib.unified_diff(
            [],
            after_lines,
            fromfile="/dev/null",
            tofile=f"b/{rel_path}",
        )
        return "".join(diff)[:MAX_FILE_SIZE]  # Truncate to prevent huge diffs
