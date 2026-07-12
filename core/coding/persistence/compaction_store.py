"""Durable, append-state compaction attempt artifacts."""

from __future__ import annotations

import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from core.coding.context.summary import (
    CompactionCheckpoint,
    CompactionResult,
    CompactionSummary,
)
from core.coding.context.workspace import now

_SAFE_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_CLOEXEC | os.O_NOFOLLOW
_MAX_ARTIFACT_BYTES = 2 * 1024 * 1024


class CompactionStoreError(RuntimeError):
    """Base error for durable compaction state."""


class CompactionConflictError(CompactionStoreError):
    """An attempt id was reused with different content or state."""


class CompactionCorruptionError(CompactionStoreError):
    """A persisted artifact violates its schema or bound identity."""


class CompactionStore:
    """Persist one monotonic JSON state machine per compaction attempt."""

    def __init__(self, root: Path, *, checkpoint_anchor_key: bytes | None = None) -> None:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink():
            raise ValueError("trusted root must not be a symlink")
        self._root = root.resolve(strict=True)
        if not self._root.is_dir():
            raise ValueError("trusted root must be a directory")
        if checkpoint_anchor_key is not None and len(checkpoint_anchor_key) < 32:
            raise ValueError("checkpoint_anchor_key must contain at least 32 bytes")
        self._checkpoint_anchor_key = checkpoint_anchor_key

    def begin(
        self,
        session_id: str,
        compaction_id: str,
        metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        _validate_id(session_id, "session")
        _validate_id(compaction_id, "compaction")
        normalized = _json_object(metadata, "metadata")
        with self._locked_directory(session_id, compaction_id) as directory_fd:
            existing = self._read_optional(directory_fd, session_id, compaction_id)
            if existing is not None:
                if existing.get("metadata") == normalized:
                    return existing
                raise CompactionConflictError("compaction attempt metadata conflict")
            timestamp = now()
            artifact = {
                "schema_version": 1,
                "session_id": session_id,
                "compaction_id": compaction_id,
                "status": "started",
                "metadata": normalized,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
            self._publish(directory_fd, compaction_id, artifact)
            return artifact

    def complete(
        self,
        session_id: str,
        compaction_id: str,
        result: CompactionResult,
        *,
        checkpoint: CompactionCheckpoint | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected = checkpoint if checkpoint is not None else result.checkpoint
        if selected is None:
            raise ValueError("completed compaction requires a checkpoint")
        if not result.applied:
            raise ValueError("completed compaction result must be applied")
        if result.compaction_id != compaction_id or selected.compaction_id != compaction_id:
            raise ValueError("compaction_id does not match attempt")
        if result.checkpoint is not None and result.checkpoint != selected:
            raise ValueError("result checkpoint does not match selected checkpoint")
        terminal = {
            "result": _serialize_result(result),
            "checkpoint": _serialize_checkpoint(selected),
            "evidence": _json_object(evidence or {}, "evidence"),
            "checkpoint_anchor": self._checkpoint_anchor(selected),
        }
        return self._transition(session_id, compaction_id, "completed", terminal)

    def fail(
        self,
        session_id: str,
        compaction_id: str,
        result: CompactionResult,
    ) -> dict[str, Any]:
        if result.applied:
            raise ValueError("failed compaction result must not be applied")
        if result.compaction_id != compaction_id:
            raise ValueError("compaction_id does not match attempt")
        return self._transition(
            session_id,
            compaction_id,
            "failed",
            {"result": _serialize_result(result)},
        )

    def load(self, session_id: str, compaction_id: str) -> dict[str, Any] | None:
        _validate_id(session_id, "session")
        _validate_id(compaction_id, "compaction")
        with self._locked_directory(session_id, compaction_id) as directory_fd:
            return self._read_optional(directory_fd, session_id, compaction_id)

    def load_latest_attempt(self, session_id: str) -> dict[str, Any] | None:
        _validate_id(session_id, "session")
        directory_fd = _open_components(self._root, ("evidence", session_id, "compactions"))
        try:
            candidates: list[dict[str, Any]] = []
            for name in os.listdir(directory_fd):
                if not name.endswith(".json"):
                    continue
                compaction_id = name[:-5]
                if not _SAFE_ID.fullmatch(compaction_id):
                    continue
                artifact = self._read_optional(directory_fd, session_id, compaction_id)
                if artifact is not None:
                    candidates.append(artifact)
            if not candidates:
                return None
            return max(
                candidates,
                key=lambda item: (str(item.get("updated_at", "")), str(item["compaction_id"])),
            )
        finally:
            os.close(directory_fd)

    def load_latest_checkpoint(self, session_id: str) -> CompactionCheckpoint | None:
        _validate_id(session_id, "session")
        directory_fd = _open_components(self._root, ("evidence", session_id, "compactions"))
        try:
            completed: list[dict[str, Any]] = []
            for name in os.listdir(directory_fd):
                if not name.endswith(".json"):
                    continue
                compaction_id = name[:-5]
                if not _SAFE_ID.fullmatch(compaction_id):
                    continue
                artifact = self._read_optional(directory_fd, session_id, compaction_id)
                if artifact is not None and artifact["status"] == "completed":
                    completed.append(artifact)
            if not completed:
                return None
            latest = max(
                completed,
                key=lambda item: (str(item["updated_at"]), str(item["compaction_id"])),
            )
            checkpoint = _deserialize_checkpoint(latest["checkpoint"])
            return checkpoint if self.verify_checkpoint(session_id, checkpoint) else None
        except (CompactionStoreError, ValidationError, ValueError, TypeError):
            return None
        finally:
            os.close(directory_fd)

    def verify_checkpoint(
        self,
        session_id: str,
        checkpoint: CompactionCheckpoint,
    ) -> bool:
        try:
            artifact = self.load(session_id, checkpoint.compaction_id)
            if artifact is None or artifact.get("status") != "completed":
                return False
            if self._checkpoint_anchor_key is None:
                return False
            stored = artifact.get("checkpoint")
            expected = _serialize_checkpoint(checkpoint)
            if stored != expected:
                return False
            expected_summary_hash = hashlib.sha256(
                (
                    f"{checkpoint.previous_summary_hash}\n"
                    f"{checkpoint.evidence_hash}\n"
                    f"{checkpoint.summary.render_for_prompt()}"
                ).encode()
            ).hexdigest()
            if not checkpoint.evidence_hash or checkpoint.summary_hash != expected_summary_hash:
                return False
            expected_anchor = self._checkpoint_anchor(checkpoint)
            stored_anchor = artifact.get("checkpoint_anchor")
            if not isinstance(stored_anchor, str) or not hmac.compare_digest(
                stored_anchor, expected_anchor
            ):
                return False
            result = artifact.get("result")
            return (
                isinstance(result, dict)
                and result.get("applied") is True
                and result.get("compaction_id") == checkpoint.compaction_id
                and result.get("checkpoint") == expected
                and checkpoint.summary.source_transcript_range
                == (checkpoint.transcript_start, checkpoint.transcript_end)
            )
        except (OSError, ValueError, CompactionStoreError, json.JSONDecodeError):
            return False

    def _transition(
        self,
        session_id: str,
        compaction_id: str,
        status: str,
        terminal: dict[str, Any],
    ) -> dict[str, Any]:
        _validate_id(session_id, "session")
        _validate_id(compaction_id, "compaction")
        with self._locked_directory(session_id, compaction_id) as directory_fd:
            existing = self._read_optional(directory_fd, session_id, compaction_id)
            if existing is None:
                raise CompactionConflictError("compaction attempt was not started")
            current = existing.get("status")
            if current == status:
                if all(existing.get(key) == value for key, value in terminal.items()):
                    return existing
                raise CompactionConflictError("terminal compaction payload conflict")
            if current != "started":
                raise CompactionConflictError(f"cannot transition {current!r} to {status!r}")
            artifact = {
                **existing,
                "status": status,
                **terminal,
                "updated_at": now(),
            }
            self._publish(directory_fd, compaction_id, artifact)
            return artifact

    def _checkpoint_anchor(self, checkpoint: CompactionCheckpoint) -> str:
        if self._checkpoint_anchor_key is None:
            return ""
        payload = json.dumps(
            _serialize_checkpoint(checkpoint),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hmac.new(self._checkpoint_anchor_key, payload, hashlib.sha256).hexdigest()

    @contextmanager
    def _locked_directory(self, session_id: str, compaction_id: str) -> Iterator[int]:
        directory_fd = _open_components(self._root, ("evidence", session_id, "compactions"))
        lock_name = f".{compaction_id}.lock"
        lock_fd = -1
        try:
            lock_fd = _open_or_create_regular(directory_fd, lock_name)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            yield directory_fd
        finally:
            if lock_fd >= 0:
                with suppress(OSError):
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            os.close(directory_fd)

    @staticmethod
    def _read_optional(
        directory_fd: int, session_id: str, compaction_id: str
    ) -> dict[str, Any] | None:
        name = f"{compaction_id}.json"
        try:
            file_fd = _open_regular(directory_fd, name, os.O_RDONLY)
        except FileNotFoundError:
            return None
        try:
            chunks: list[bytes] = []
            total = 0
            while chunk := os.read(file_fd, 64 * 1024):
                total += len(chunk)
                if total > _MAX_ARTIFACT_BYTES:
                    raise CompactionCorruptionError("compaction artifact exceeds size limit")
                chunks.append(chunk)
            try:
                value = json.loads(b"".join(chunks).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise CompactionCorruptionError("compaction artifact is invalid JSON") from exc
            if not isinstance(value, dict):
                raise CompactionCorruptionError("compaction artifact must be a JSON object")
            _validate_artifact(value, session_id, compaction_id)
            return value
        finally:
            os.close(file_fd)

    @staticmethod
    def _publish(directory_fd: int, compaction_id: str, artifact: dict[str, Any]) -> None:
        target = f"{compaction_id}.json"
        _reject_unsafe_target(directory_fd, target)
        payload = json.dumps(
            artifact,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        temp_name = ""
        temp_fd = -1
        try:
            for _ in range(100):
                temp_name = f".{compaction_id}.{secrets.token_hex(16)}.tmp"
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
                raise OSError("unable to allocate compaction temporary file")
            os.fchmod(temp_fd, 0o600)
            _write_all(temp_fd, payload)
            os.fsync(temp_fd)
            os.close(temp_fd)
            temp_fd = -1
            os.replace(temp_name, target, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            temp_name = ""
            try:
                os.fsync(directory_fd)
            except OSError:
                if _read_regular_bytes(directory_fd, target) != payload:
                    raise CompactionStoreError(
                        "committed compaction payload could not be confirmed"
                    ) from None
        finally:
            if temp_fd >= 0:
                os.close(temp_fd)
            if temp_name:
                with suppress(FileNotFoundError):
                    os.unlink(temp_name, dir_fd=directory_fd)


def _open_components(root: Path, components: tuple[str, ...]) -> int:
    directory_fd = os.open(root, _DIRECTORY_FLAGS)
    try:
        os.fchmod(directory_fd, 0o700)
        for component in components:
            try:
                os.mkdir(component, 0o700, dir_fd=directory_fd)
                os.fsync(directory_fd)
            except FileExistsError:
                pass
            try:
                next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=directory_fd)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    raise ValueError(f"symlink path component rejected: {component}") from exc
                raise
            os.fchmod(next_fd, 0o700)
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except BaseException:
        os.close(directory_fd)
        raise


def _open_or_create_regular(directory_fd: int, name: str) -> int:
    try:
        file_fd = os.open(
            name,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | _FILE_FLAGS,
            0o600,
            dir_fd=directory_fd,
        )
        os.fsync(file_fd)
        os.fsync(directory_fd)
    except FileExistsError:
        return _open_regular(directory_fd, name, os.O_RDWR)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"symlink file rejected: {name}") from exc
        raise
    _validate_regular(file_fd, name)
    os.fchmod(file_fd, 0o600)
    return file_fd


def _open_regular(directory_fd: int, name: str, flags: int) -> int:
    try:
        file_fd = os.open(name, flags | _FILE_FLAGS, dir_fd=directory_fd)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError(f"symlink file rejected: {name}") from exc
        raise
    try:
        _validate_regular(file_fd, name)
        os.fchmod(file_fd, 0o600)
    except BaseException:
        os.close(file_fd)
        raise
    return file_fd


def _validate_regular(file_fd: int, name: str) -> None:
    metadata = os.fstat(file_fd)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"non-regular file rejected: {name}")
    if metadata.st_nlink != 1:
        raise ValueError(f"hardlink file rejected: {name}")


def _reject_unsafe_target(directory_fd: int, name: str) -> None:
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(metadata.st_mode):
        raise ValueError(f"symlink file rejected: {name}")
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"non-regular file rejected: {name}")
    if metadata.st_nlink != 1:
        raise ValueError(f"hardlink file rejected: {name}")


def _write_all(file_fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(file_fd, view)
        if written == 0:
            raise OSError("short write")
        view = view[written:]


def _read_regular_bytes(directory_fd: int, name: str) -> bytes:
    file_fd = _open_regular(directory_fd, name, os.O_RDONLY)
    try:
        chunks: list[bytes] = []
        while chunk := os.read(file_fd, 64 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(file_fd)


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"invalid {label} id")


def _json_object(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be JSON-safe") from exc
    if not isinstance(decoded, dict):
        raise TypeError(f"{label} must be a JSON object")
    return decoded


def _serialize_checkpoint(checkpoint: CompactionCheckpoint) -> dict[str, Any]:
    return {
        "compaction_id": checkpoint.compaction_id,
        "transcript_start": checkpoint.transcript_start,
        "transcript_end": checkpoint.transcript_end,
        "summary": checkpoint.summary.model_dump(mode="json"),
        "summary_hash": checkpoint.summary_hash,
        "previous_summary_hash": checkpoint.previous_summary_hash,
        "evidence_hash": checkpoint.evidence_hash,
        "prefix_hash": checkpoint.prefix_hash,
    }


def _serialize_result(result: CompactionResult) -> dict[str, Any]:
    return {
        "applied": result.applied,
        "checkpoint": (
            _serialize_checkpoint(result.checkpoint) if result.checkpoint is not None else None
        ),
        "before_tokens": result.before_tokens,
        "after_tokens": result.after_tokens,
        "archived_items": result.archived_items,
        "reason": result.reason,
        "compaction_id": result.compaction_id,
        "trigger": result.trigger,
        "retryable": result.retryable,
        "cooldown_until": result.cooldown_until,
    }


def _deserialize_checkpoint(value: object) -> CompactionCheckpoint:
    if not isinstance(value, dict):
        raise CompactionCorruptionError("checkpoint must be an object")
    expected = {
        "compaction_id", "transcript_start", "transcript_end", "summary", "summary_hash",
        "previous_summary_hash", "evidence_hash", "prefix_hash",
    }
    if set(value) != expected:
        raise CompactionCorruptionError("checkpoint fields are invalid")
    try:
        return CompactionCheckpoint(
            compaction_id=_required_str(value["compaction_id"], "checkpoint compaction_id"),
            transcript_start=_required_int(value["transcript_start"], "transcript_start"),
            transcript_end=_required_int(value["transcript_end"], "transcript_end"),
            summary=CompactionSummary.model_validate(value["summary"]),
            summary_hash=_required_str(value["summary_hash"], "summary_hash"),
            previous_summary_hash=_required_str(
                value["previous_summary_hash"], "previous_summary_hash", allow_empty=True
            ),
            evidence_hash=_required_str(value["evidence_hash"], "evidence_hash"),
            prefix_hash=_required_str(value["prefix_hash"], "prefix_hash", allow_empty=True),
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise CompactionCorruptionError("checkpoint payload is invalid") from exc


def _validate_artifact(value: dict[str, Any], session_id: str, compaction_id: str) -> None:
    base = {
        "schema_version", "session_id", "compaction_id", "status", "metadata",
        "created_at", "updated_at",
    }
    if value.get("schema_version") != 1:
        raise CompactionCorruptionError("unsupported compaction artifact schema")
    if value.get("session_id") != session_id or value.get("compaction_id") != compaction_id:
        raise CompactionCorruptionError("compaction artifact identity mismatch")
    status = value.get("status")
    required = set(base)
    if status == "completed":
        required.update({"result", "checkpoint", "evidence", "checkpoint_anchor"})
    elif status == "failed":
        required.add("result")
    elif status != "started":
        raise CompactionCorruptionError("compaction artifact status is invalid")
    if set(value) != required:
        raise CompactionCorruptionError("compaction artifact fields are invalid")
    if not isinstance(value["metadata"], dict):
        raise CompactionCorruptionError("compaction metadata must be an object")
    _required_str(value["created_at"], "created_at")
    _required_str(value["updated_at"], "updated_at")
    if status in {"completed", "failed"}:
        _validate_result(value["result"], compaction_id, applied=status == "completed")
    if status == "completed":
        checkpoint = _deserialize_checkpoint(value["checkpoint"])
        if checkpoint.compaction_id != compaction_id:
            raise CompactionCorruptionError("checkpoint identity mismatch")
        if value["result"]["checkpoint"] != value["checkpoint"]:
            raise CompactionCorruptionError("result checkpoint mismatch")
        if not isinstance(value["evidence"], dict):
            raise CompactionCorruptionError("compaction evidence must be an object")
        anchor = value["checkpoint_anchor"]
        if not isinstance(anchor, str) or (anchor and not re.fullmatch(r"[0-9a-f]{64}", anchor)):
            raise CompactionCorruptionError("checkpoint anchor is invalid")


def _validate_result(value: object, compaction_id: str, *, applied: bool) -> None:
    if not isinstance(value, dict):
        raise CompactionCorruptionError("compaction result must be an object")
    expected = {
        "applied", "checkpoint", "before_tokens", "after_tokens", "archived_items", "reason",
        "compaction_id", "trigger", "retryable", "cooldown_until",
    }
    if set(value) != expected or value["applied"] is not applied:
        raise CompactionCorruptionError("compaction result fields are invalid")
    if value["compaction_id"] != compaction_id:
        raise CompactionCorruptionError("compaction result identity mismatch")
    for field in ("before_tokens", "after_tokens", "archived_items"):
        amount = value[field]
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise CompactionCorruptionError(f"{field} is invalid")
    _required_str(value["trigger"], "trigger")
    if not isinstance(value["reason"], str) or not isinstance(value["retryable"], bool):
        raise CompactionCorruptionError("compaction result scalar fields are invalid")
    cooldown = value["cooldown_until"]
    if cooldown is not None and (
        not isinstance(cooldown, int | float) or isinstance(cooldown, bool)
    ):
        raise CompactionCorruptionError("cooldown_until is invalid")
    if applied and not isinstance(value["checkpoint"], dict):
        raise CompactionCorruptionError("completed result checkpoint is missing")
    if not applied and value["checkpoint"] is not None:
        raise CompactionCorruptionError("failed result checkpoint must be empty")


def _required_str(value: object, label: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise CompactionCorruptionError(f"{label} is invalid")
    return value


def _required_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CompactionCorruptionError(f"{label} is invalid")
    return value
