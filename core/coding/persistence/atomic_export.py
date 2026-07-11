"""Locked atomic JSONL publication with recoverable pre-commit failures."""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import secrets
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress

EXPORT_NAME = "transcript.jsonl"
LOCK_NAME = ".export.lock"

_FILE_FLAGS = os.O_CLOEXEC | os.O_NOFOLLOW
_LOGGER = logging.getLogger(__name__)


def publish_jsonl(directory_fd: int, payload_factory: Callable[[], bytes]) -> None:
    """Build and publish one payload while holding the session export lock."""
    with _export_lock(directory_fd):
        _publish_locked(directory_fd, payload_factory())


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
    body_succeeded = False
    primary: BaseException | None = None
    try:
        _validate_file(lock_fd, LOCK_NAME)
        os.fchmod(lock_fd, 0o600)
        if created:
            os.fsync(lock_fd)
            os.fsync(directory_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        acquired = True
        yield
        body_succeeded = True
    except BaseException as exc:
        primary = exc
        raise
    finally:
        teardown_errors: list[tuple[str, Exception]] = []
        if acquired:
            _attempt_teardown(
                teardown_errors,
                "export lock release",
                lambda: fcntl.flock(lock_fd, fcntl.LOCK_UN),
            )
        _attempt_teardown(
            teardown_errors,
            "export lock file close",
            lambda: os.close(lock_fd),
        )
        _resolve_teardown_errors(
            teardown_errors,
            committed=body_succeeded,
            primary=primary,
        )


def _publish_locked(directory_fd: int, payload: bytes) -> None:
    target_fd = _open_optional_target(directory_fd)
    temp_name = ""
    temp_fd = -1
    backup_name = ""
    backup_fd = -1
    preserve_backup = False
    committed = False
    primary: BaseException | None = None
    try:
        temp_name, temp_fd = _open_unique_file(directory_fd, "tmp")
        os.fchmod(temp_fd, 0o600)
        _write_all(temp_fd, payload)
        os.fsync(temp_fd)
        try:
            os.close(temp_fd)
        except BaseException:
            if not _fd_is_open(temp_fd):
                temp_fd = -1
            raise
        else:
            temp_fd = -1

        if target_fd >= 0:
            backup_name, backup_fd = _open_unique_file(directory_fd, "bak")
            os.fchmod(backup_fd, 0o600)
            _copy_file(target_fd, backup_fd)
            os.fsync(backup_fd)
            try:
                os.close(backup_fd)
            except BaseException:
                if not _fd_is_open(backup_fd):
                    backup_fd = -1
                raise
            else:
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
            committed = True
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
            raise
    except BaseException as exc:
        primary = exc
        raise
    finally:
        teardown_errors: list[tuple[str, Exception]] = []
        if target_fd >= 0:
            _attempt_teardown(
                teardown_errors,
                "export target close",
                lambda: os.close(target_fd),
            )
        if temp_fd >= 0:
            _attempt_teardown(
                teardown_errors,
                "export temp close",
                lambda: os.close(temp_fd),
            )
        if backup_fd >= 0:
            _attempt_teardown(
                teardown_errors,
                "export backup close",
                lambda: os.close(backup_fd),
            )
        if temp_name:
            _attempt_teardown(
                teardown_errors,
                "export temp cleanup",
                lambda: _remove_name(directory_fd, temp_name),
            )
        if backup_name and not preserve_backup:
            _attempt_teardown(
                teardown_errors,
                "export backup cleanup",
                lambda: _remove_name(directory_fd, backup_name),
            )
        _resolve_teardown_errors(
            teardown_errors,
            committed=committed,
            primary=primary,
        )


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
    except BaseException as exc:
        teardown_errors: list[tuple[str, Exception]] = []
        _attempt_teardown(
            teardown_errors,
            f"rejected file close ({name})",
            lambda: os.close(file_fd),
        )
        _resolve_teardown_errors(teardown_errors, committed=False, primary=exc)
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


def _remove_name(directory_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        return
    os.fsync(directory_fd)


def _restore_target(directory_fd: int, source_fd: int) -> None:
    recovery_name, recovery_fd = _open_unique_file(directory_fd, "bak")
    primary: BaseException | None = None
    try:
        os.fchmod(recovery_fd, 0o600)
        _copy_file(source_fd, recovery_fd)
        os.fsync(recovery_fd)
        try:
            os.close(recovery_fd)
        except BaseException:
            if not _fd_is_open(recovery_fd):
                recovery_fd = -1
            raise
        else:
            recovery_fd = -1
        os.replace(
            recovery_name,
            EXPORT_NAME,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        recovery_name = ""
    except BaseException as exc:
        primary = exc
        raise
    finally:
        teardown_errors: list[tuple[str, Exception]] = []
        if recovery_fd >= 0:
            _attempt_teardown(
                teardown_errors,
                "rollback recovery close",
                lambda: os.close(recovery_fd),
            )
        if recovery_name:
            _attempt_teardown(
                teardown_errors,
                "rollback recovery cleanup",
                lambda: _remove_name(directory_fd, recovery_name),
            )
        _resolve_teardown_errors(
            teardown_errors,
            committed=False,
            primary=primary,
        )


def _attempt_teardown(
    errors: list[tuple[str, Exception]],
    label: str,
    action: Callable[[], None],
) -> None:
    try:
        action()
    except Exception as exc:
        errors.append((label, exc))


def _fd_is_open(file_fd: int) -> bool:
    try:
        os.fstat(file_fd)
    except OSError as exc:
        return exc.errno != errno.EBADF
    except Exception:
        return True
    return True


def _best_effort_log(label: str, error: Exception) -> None:
    with suppress(Exception):
        _LOGGER.warning(
            "transcript export teardown failed during %s: %s",
            label,
            error,
            exc_info=(type(error), error, error.__traceback__),
        )


def _resolve_teardown_errors(
    errors: list[tuple[str, Exception]],
    *,
    committed: bool,
    primary: BaseException | None,
) -> None:
    if not errors:
        return
    for label, error in errors:
        _best_effort_log(label, error)
    if not committed and primary is None:
        raise errors[0][1]
