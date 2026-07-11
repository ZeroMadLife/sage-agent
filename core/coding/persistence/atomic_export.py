"""Locked atomic JSONL publication with recoverable pre-commit failures."""

from __future__ import annotations

import errno
import fcntl
import os
import re
import secrets
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress

EXPORT_NAME = "transcript.jsonl"
LOCK_NAME = ".export.lock"

_FILE_FLAGS = os.O_CLOEXEC | os.O_NOFOLLOW
_BACKUP_PATTERN = re.compile(r"\.transcript\.jsonl\.[0-9a-f]{32}\.bak\Z")


def publish_jsonl(directory_fd: int, payload: bytes) -> None:
    """Publish one payload while holding the session export lock."""
    with _export_lock(directory_fd):
        _publish_locked(directory_fd, payload)


@contextmanager
def _export_lock(directory_fd: int) -> Iterator[None]:
    created = False
    try:
        lock_fd = os.open(
            LOCK_NAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
            0o600,
            dir_fd=directory_fd,
        )
        created = True
    except FileExistsError:
        lock_fd = _open_verified_file(directory_fd, LOCK_NAME)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"symlink file rejected: {LOCK_NAME}") from exc
        raise

    acquired = False
    try:
        _validate_file(lock_fd, LOCK_NAME)
        os.fchmod(lock_fd, 0o600)
        if created:
            os.fsync(lock_fd)
            os.fsync(directory_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        acquired = True
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _publish_locked(directory_fd: int, payload: bytes) -> None:
    target_fd = _open_optional_target(directory_fd)
    temp_name = ""
    temp_fd = -1
    backup_name = ""
    backup_fd = -1
    preserve_backup = False
    try:
        temp_name, temp_fd = _open_unique_file(directory_fd, "tmp")
        os.fchmod(temp_fd, 0o600)
        _write_all(temp_fd, payload)
        os.fsync(temp_fd)
        os.close(temp_fd)
        temp_fd = -1

        if target_fd >= 0:
            backup_name, backup_fd = _open_unique_file(directory_fd, "bak")
            os.fchmod(backup_fd, 0o600)
            _copy_file(target_fd, backup_fd)
            os.fsync(backup_fd)
            os.close(backup_fd)
            backup_fd = -1
            os.fsync(directory_fd)

        replaced = False
        try:
            os.replace(
                temp_name,
                EXPORT_NAME,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            temp_name = ""
            replaced = True
            os.fsync(directory_fd)
        except Exception as publish_error:
            if replaced:
                try:
                    if target_fd >= 0:
                        _restore_target(directory_fd, target_fd)
                    else:
                        os.unlink(EXPORT_NAME, dir_fd=directory_fd)
                    os.fsync(directory_fd)
                except Exception as rollback_error:
                    preserve_backup = bool(backup_name)
                    raise rollback_error from publish_error
            backup_name = _best_effort_remove(directory_fd, backup_name)
            preserve_backup = bool(backup_name)
            raise

        backup_name = _best_effort_remove(directory_fd, backup_name)
        preserve_backup = bool(backup_name)
        _cleanup_stale_backups(directory_fd)
    finally:
        if target_fd >= 0:
            os.close(target_fd)
        if temp_fd >= 0:
            os.close(temp_fd)
        if backup_fd >= 0:
            os.close(backup_fd)
        if temp_name:
            _best_effort_remove(directory_fd, temp_name)
        if backup_name and not preserve_backup:
            _best_effort_remove(directory_fd, backup_name)


def _open_optional_target(directory_fd: int) -> int:
    try:
        return _open_verified_file(
            directory_fd,
            EXPORT_NAME,
            flags=os.O_RDONLY | os.O_NONBLOCK,
        )
    except FileNotFoundError:
        return -1


def _open_verified_file(
    directory_fd: int,
    name: str,
    *,
    flags: int = os.O_RDWR,
) -> int:
    try:
        file_fd = os.open(name, flags | _FILE_FLAGS, dir_fd=directory_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"symlink file rejected: {name}") from exc
        raise
    try:
        _validate_file(file_fd, name)
    except Exception:
        os.close(file_fd)
        raise
    return file_fd


def _validate_file(file_fd: int, name: str) -> None:
    metadata = os.fstat(file_fd)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"non-regular file rejected: {name}")
    if metadata.st_nlink != 1:
        raise ValueError(f"hardlink file rejected: {name}")


def _open_unique_file(directory_fd: int, suffix: str) -> tuple[str, int]:
    for _ in range(100):
        candidate = f".{EXPORT_NAME}.{secrets.token_hex(16)}.{suffix}"
        try:
            file_fd = os.open(
                candidate,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
                0o600,
                dir_fd=directory_fd,
            )
            return candidate, file_fd
        except FileExistsError:
            continue
    raise OSError(f"unable to allocate transcript export {suffix} file")


def _copy_file(source_fd: int, destination_fd: int) -> None:
    os.lseek(source_fd, 0, os.SEEK_SET)
    while chunk := os.read(source_fd, 64 * 1024):
        _write_all(destination_fd, chunk)


def _write_all(file_fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(file_fd, view)
        if written == 0:
            raise OSError("short write")
        view = view[written:]


def _best_effort_remove(directory_fd: int, name: str) -> str:
    if not name:
        return ""
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        return ""
    except OSError:
        return name
    with suppress(OSError):
        os.fsync(directory_fd)
    return ""


def _cleanup_stale_backups(directory_fd: int) -> None:
    try:
        candidates = os.listdir(directory_fd)
    except OSError:
        return
    for candidate in candidates:
        if _BACKUP_PATTERN.fullmatch(candidate) is None:
            continue
        try:
            candidate_fd = _open_verified_file(
                directory_fd,
                candidate,
                flags=os.O_RDONLY | os.O_NONBLOCK,
            )
        except (OSError, ValueError):
            continue
        os.close(candidate_fd)
        _best_effort_remove(directory_fd, candidate)


def _restore_target(directory_fd: int, source_fd: int) -> None:
    recovery_name, recovery_fd = _open_unique_file(directory_fd, "bak")
    try:
        os.fchmod(recovery_fd, 0o600)
        _copy_file(source_fd, recovery_fd)
        os.fsync(recovery_fd)
    except Exception:
        os.close(recovery_fd)
        with suppress(FileNotFoundError):
            os.unlink(recovery_name, dir_fd=directory_fd)
        raise
    os.close(recovery_fd)
    os.replace(
        recovery_name,
        EXPORT_NAME,
        src_dir_fd=directory_fd,
        dst_dir_fd=directory_fd,
    )
