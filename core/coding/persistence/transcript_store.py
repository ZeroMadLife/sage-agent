"""Canonical append-only transcript persistence."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_CLOEXEC | os.O_NOFOLLOW
_LOCK_NAME = ".transcript.lock"
_TRANSCRIPT_NAME = "transcript.jsonl"


class TranscriptCorruptionError(RuntimeError):
    """Raised when corruption appears before the repairable transcript tail."""


@dataclass(frozen=True)
class TranscriptItem:
    """One canonical transcript entry."""

    message_id: str
    role: str
    content: str
    run_id: str = ""
    turn_id: str = ""
    call_id: str = ""
    artifact_ref: str = ""
    created_at: str = ""


class TranscriptStore:
    """Append transcript entries once and preserve all existing valid lines."""

    def __init__(self, root: Path, session_id: str) -> None:
        _validate_scope_id(session_id, "session")
        self._root = _trusted_root(root)
        self._components = ("evidence", session_id)
        self.path = root.joinpath(*self._components, _TRANSCRIPT_NAME)
        self._lock = threading.RLock()
        with self._exclusive_directory() as directory_fd:
            self._message_ids = {
                item.message_id for item in self._read_items(directory_fd, repair_tail=True)
            }

    def append(self, item: TranscriptItem) -> bool:
        """Append ``item`` unless its message id was already persisted."""
        line = (json.dumps(asdict(item), ensure_ascii=False, sort_keys=True) + "\n").encode()
        with self._lock, self._exclusive_directory() as directory_fd:
            self._message_ids = {
                entry.message_id for entry in self._read_items(directory_fd, repair_tail=True)
            }
            if item.message_id in self._message_ids:
                return False

            transcript_fd = _open_file(
                directory_fd,
                _TRANSCRIPT_NAME,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT | _FILE_FLAGS,
                0o600,
            )
            try:
                os.fchmod(transcript_fd, 0o600)
                _write_all(transcript_fd, line)
                os.fsync(transcript_fd)
            except Exception:
                self._message_ids = {
                    entry.message_id
                    for entry in self._read_items(directory_fd, repair_tail=True)
                }
                raise
            finally:
                os.close(transcript_fd)

            self._message_ids.add(item.message_id)
            return True

    def read_all(self) -> list[TranscriptItem]:
        """Read valid transcript lines, repairing a malformed final tail."""
        with self._lock, self._exclusive_directory() as directory_fd:
            items = self._read_items(directory_fd, repair_tail=True)
            self._message_ids = {item.message_id for item in items}
            return items

    @contextmanager
    def _exclusive_directory(self) -> Iterator[int]:
        directory_fd = _open_directory(self._root, self._components, create=True)
        lock_fd = -1
        try:
            lock_fd = _open_file(
                directory_fd,
                _LOCK_NAME,
                os.O_RDWR | os.O_CREAT | _FILE_FLAGS,
                0o600,
            )
            os.fchmod(lock_fd, 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield directory_fd
        finally:
            if lock_fd >= 0:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            os.close(directory_fd)

    def _read_items(self, directory_fd: int, *, repair_tail: bool) -> list[TranscriptItem]:
        try:
            transcript_fd = _open_file(
                directory_fd,
                _TRANSCRIPT_NAME,
                os.O_RDWR | _FILE_FLAGS,
            )
        except FileNotFoundError:
            return []

        try:
            os.fchmod(transcript_fd, 0o600)
            data = _read_all(transcript_fd)
            return self._parse_items(
                directory_fd,
                transcript_fd,
                data,
                repair_tail=repair_tail,
            )
        finally:
            os.close(transcript_fd)

    def _parse_items(
        self,
        directory_fd: int,
        transcript_fd: int,
        data: bytes,
        *,
        repair_tail: bool,
    ) -> list[TranscriptItem]:
        lines = data.splitlines(keepends=True)
        nonempty_indexes = [index for index, line in enumerate(lines) if line.strip()]
        last_nonempty = nonempty_indexes[-1] if nonempty_indexes else -1
        items: list[TranscriptItem] = []
        offset = 0

        for index, raw_line in enumerate(lines):
            if not raw_line.strip():
                offset += len(raw_line)
                continue
            try:
                payload = cast(dict[str, str], json.loads(raw_line.decode("utf-8")))
                if not isinstance(payload, dict):
                    raise TypeError("transcript entry must be an object")
                items.append(TranscriptItem(**payload))
            except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
                line_number = index + 1
                if repair_tail and index == last_nonempty:
                    self._repair_tail(directory_fd, transcript_fd, data[offset:], offset)
                    return items
                raise TranscriptCorruptionError(
                    f"corrupt transcript {self.path} at line {line_number}"
                ) from exc
            offset += len(raw_line)
        if data and not data.endswith(b"\n"):
            os.lseek(transcript_fd, 0, os.SEEK_END)
            _write_all(transcript_fd, b"\n")
            os.fsync(transcript_fd)
        return items

    def _repair_tail(
        self,
        directory_fd: int,
        transcript_fd: int,
        damaged: bytes,
        valid_size: int,
    ) -> None:
        quarantine_fd = -1
        for suffix in range(1_000):
            name = f"{_TRANSCRIPT_NAME}.torn" if suffix == 0 else f"{_TRANSCRIPT_NAME}.torn.{suffix}"
            try:
                quarantine_fd = _open_file(
                    directory_fd,
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
                    0o600,
                )
                break
            except FileExistsError:
                continue
        if quarantine_fd < 0:
            raise OSError("unable to allocate transcript tail quarantine")
        try:
            os.fchmod(quarantine_fd, 0o600)
            _write_all(quarantine_fd, damaged)
            os.fsync(quarantine_fd)
        finally:
            os.close(quarantine_fd)
        os.ftruncate(transcript_fd, valid_size)
        os.fsync(transcript_fd)


def _trusted_root(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise ValueError(f"trusted root must not be a symlink: {root}")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"trusted root is not a directory: {root}")
    return resolved


def _open_directory(root: Path, components: tuple[str, ...], *, create: bool) -> int:
    directory_fd = os.open(root, _DIRECTORY_FLAGS)
    try:
        for component in components:
            if create:
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


def _open_file(directory_fd: int, name: str, flags: int, mode: int = 0o600) -> int:
    try:
        return os.open(name, flags, mode, dir_fd=directory_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"symlink file rejected: {name}") from exc
        raise


def _read_all(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while chunk := os.read(fd, 64 * 1024):
        chunks.append(chunk)
    return b"".join(chunks)


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
