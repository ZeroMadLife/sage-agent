"""Durable full-result artifacts with bounded transcript previews."""

from __future__ import annotations

import errno
import os
import secrets
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

PERSIST_THRESHOLD_BYTES = 16 * 1024
PREVIEW_LINES = 200
PREVIEW_CHARS = 12_000

_HEAD_LINES = 120
_TAIL_LINES = PREVIEW_LINES - _HEAD_LINES
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_CLOEXEC | os.O_NOFOLLOW


@dataclass(frozen=True)
class ArchivedToolResult:
    """A persisted tool result and its bounded preview."""

    artifact_ref: str
    artifact_path: Path
    preview: str
    original_chars: int
    truncated: bool


class ToolResultStore:
    """Persist complete tool output before deriving a bounded preview."""

    def __init__(self, root: Path, session_id: str, run_id: str) -> None:
        _validate_scope_id(session_id, "session")
        _validate_scope_id(run_id, "run")
        self._root = _trusted_root(root)
        self._components = ("evidence", session_id, "runs", run_id, "tool-results")
        self.root = root.joinpath(*self._components)
        _reject_existing_symlinks(self._root, self._components)

    def archive(self, call_id: str, content: str) -> ArchivedToolResult:
        """Atomically persist ``content``, then return its transcript preview."""
        _validate_scope_id(call_id, "call")
        artifact_ref = f"{call_id}.txt"
        directory_fd = _open_directory(self._root, self._components)
        try:
            _reject_symlink_file(directory_fd, artifact_ref)
            self._replace_artifact(directory_fd, artifact_ref, content)
        finally:
            os.close(directory_fd)

        preview = _bounded_preview(content, call_id)
        return ArchivedToolResult(
            artifact_ref=artifact_ref,
            artifact_path=self.root / artifact_ref,
            preview=preview,
            original_chars=len(content),
            truncated=preview != content,
        )

    def _replace_artifact(self, directory_fd: int, artifact_ref: str, content: str) -> None:
        temp_name = ""
        temp_fd = -1
        try:
            for _ in range(100):
                temp_name = f".{artifact_ref}.{secrets.token_hex(8)}.tmp"
                try:
                    temp_fd = os.open(
                        temp_name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
                        0o600,
                        dir_fd=directory_fd,
                    )
                    break
                except FileExistsError:
                    continue
            if temp_fd < 0:
                raise OSError("unable to allocate tool result temporary file")
            os.fchmod(temp_fd, 0o600)
            _write_all(temp_fd, content.encode("utf-8"))
            os.fsync(temp_fd)
            os.close(temp_fd)
            temp_fd = -1
            os.replace(temp_name, artifact_ref, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            temp_name = ""
        finally:
            if temp_fd >= 0:
                os.close(temp_fd)
            if temp_name:
                with suppress(FileNotFoundError):
                    os.unlink(temp_name, dir_fd=directory_fd)


def _bounded_preview(content: str, call_id: str) -> str:
    lines = content.splitlines(keepends=True)
    selected = content
    if len(lines) > PREVIEW_LINES:
        selected = "".join(lines[:_HEAD_LINES] + lines[-_TAIL_LINES:])

    if selected == content and len(selected) <= PREVIEW_CHARS:
        return content

    marker = f"\n[full result: {call_id}]"
    budget = PREVIEW_CHARS - len(marker)
    if len(selected) > budget:
        head_chars = budget * _HEAD_LINES // PREVIEW_LINES
        tail_chars = budget - head_chars
        selected = selected[:head_chars] + selected[-tail_chars:]
    return selected + marker


def _trusted_root(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError(f"trusted root must not be a symlink: {root}")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"trusted root is not a directory: {root}")
    return resolved


def _reject_existing_symlinks(root: Path, components: tuple[str, ...]) -> None:
    current = root
    for component in components:
        current = current / component
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError(f"symlink path component rejected: {current}")


def _open_directory(root: Path, components: tuple[str, ...]) -> int:
    directory_fd = os.open(root, _DIRECTORY_FLAGS)
    try:
        for component in components:
            with suppress(FileExistsError):
                os.mkdir(component, mode=0o700, dir_fd=directory_fd)
            try:
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError(f"symlink path component rejected: {component}") from exc
                raise
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except Exception:
        os.close(directory_fd)
        raise


def _reject_symlink_file(directory_fd: int, name: str) -> None:
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"symlink file rejected: {name}")


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written == 0:
            raise OSError("short write")
        view = view[written:]


def _validate_scope_id(value: str, label: str) -> None:
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"invalid {label} id")
