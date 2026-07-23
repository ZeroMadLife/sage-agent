"""Atomic lifecycle registry for immutable public knowledge packages."""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from public_agent.corpus import PublicPackage

_SAFE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SAFE_ACTOR = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._-]{0,127}")
_MAX_PACKAGE_BYTES = 2 * 1024 * 1024
_MAX_REGISTRY_BYTES = 8 * 1024 * 1024
_UTC = timezone.utc  # noqa: UP017 - root package controller supports Python 3.10


class PublishedPackageError(RuntimeError):
    """A package lifecycle operation could not be completed safely."""


@dataclass(frozen=True, slots=True)
class ActivePackageRef:
    package_id: str
    revision: str
    digest: str


class PublishedPackageProvider:
    """Read-only resolver that freezes one validated package per call."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._cached_ref: ActivePackageRef | None = None
        self._cached_package: PublicPackage | None = None

    def current(self) -> PublicPackage:
        state = _read_registry(self.root)
        ref = _active_ref(state)
        if ref == self._cached_ref and self._cached_package is not None:
            return self._cached_package
        package = _load_registered_package(self.root, ref)
        self._cached_ref = ref
        self._cached_package = package
        return package


class PublishedPackageRegistry:
    """Root-owned writer for staged, active, inactive, and revoked revisions."""

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.root = Path(root)
        self.clock = clock or (lambda: datetime.now(_UTC).isoformat())

    def bootstrap(self, package_path: str | Path, *, actor: str) -> dict[str, Any]:
        if self.registry_path.exists():
            return self.status()
        staged = self.stage_path(package_path, actor=actor)
        return self.activate(
            str(staged["package_id"]),
            str(staged["revision"]),
            expected_active_revision=None,
            actor=actor,
        )

    @property
    def registry_path(self) -> Path:
        return self.root / "registry.json"

    @property
    def lock_path(self) -> Path:
        return self.root / "registry.lock"

    def stage_path(self, package_path: str | Path, *, actor: str) -> dict[str, Any]:
        source = Path(package_path)
        if source.is_symlink() or not source.is_file():
            raise PublishedPackageError("公开资料包来源必须是普通文件")
        data = _read_bounded(source, _MAX_PACKAGE_BYTES, "公开资料包")
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            raise PublishedPackageError("公开资料包不是有效 JSON") from exc
        if not isinstance(payload, dict):
            raise PublishedPackageError("公开资料包必须是 JSON object")
        return self.stage_payload(payload, actor=actor)

    def stage_payload(self, payload: Mapping[str, Any], *, actor: str) -> dict[str, Any]:
        actor = _validate_actor(actor)
        canonical = (
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
        if len(canonical) > _MAX_PACKAGE_BYTES:
            raise PublishedPackageError("公开资料包超过 2 MiB")
        self._ensure_root()
        with tempfile.NamedTemporaryFile(dir=self.root, suffix=".json", delete=False) as stream:
            stream.write(canonical)
            stream.flush()
            os.fsync(stream.fileno())
            candidate = Path(stream.name)
        try:
            try:
                package = PublicPackage.load(candidate)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise PublishedPackageError(f"公开资料包校验失败: {exc}") from exc
        finally:
            candidate.unlink(missing_ok=True)
        _validate_ref(package.package_id, "package_id")
        _validate_ref(package.revision, "revision")
        with self._locked():
            state = self._load_or_default()
            existing = _find_record(state, package.package_id, package.revision)
            if existing is not None:
                if existing.get("digest") != package.digest:
                    raise PublishedPackageError("同一 revision 已存在不同内容，禁止覆盖")
                return _operation_result(state, existing, "already-staged")
            destination = _package_path(self.root, package.package_id, package.revision)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            if destination.is_symlink():
                raise PublishedPackageError("公开资料包目标不能是符号链接")
            _atomic_write(destination, canonical, mode=0o644, overwrite=False)
            now = self.clock()
            record: dict[str, Any] = {
                "package_id": package.package_id,
                "revision": package.revision,
                "digest": package.digest,
                "state": "candidate",
                "staged_at": now,
                "staged_by": actor,
                "activation_sequence": 0,
            }
            state["packages"].append(record)
            _append_event(state, "staged", record, actor=actor, timestamp=now)
            self._write_state(state)
            return _operation_result(state, record, "staged")

    def activate(
        self,
        package_id: str,
        revision: str,
        *,
        expected_active_revision: str | None,
        actor: str,
    ) -> dict[str, Any]:
        package_id = _validate_ref(package_id, "package_id")
        revision = _validate_ref(revision, "revision")
        actor = _validate_actor(actor)
        with self._locked():
            state = _read_registry(self.root, require_active=False)
            _require_expected_active(state, expected_active_revision)
            target = _find_record(state, package_id, revision)
            if target is None:
                raise PublishedPackageError("待发布 revision 不存在")
            if target.get("state") == "revoked":
                raise PublishedPackageError("已撤回 revision 不能重新发布")
            ref = ActivePackageRef(package_id, revision, str(target["digest"]))
            _load_registered_package(self.root, ref)
            current = _active_record(state)
            if current is target:
                return _operation_result(state, target, "already-active")
            now = self.clock()
            if current is not None:
                current["state"] = "inactive"
            sequence = int(state.get("activation_sequence", 0)) + 1
            state["activation_sequence"] = sequence
            target["state"] = "active"
            target["activated_at"] = now
            target["activated_by"] = actor
            target["activation_sequence"] = sequence
            state["active"] = _ref_dict(ref)
            _append_event(state, "activated", target, actor=actor, timestamp=now)
            self._write_state(state)
            return _operation_result(state, target, "activated")

    def revoke(
        self,
        package_id: str,
        revision: str,
        *,
        expected_active_revision: str | None,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        package_id = _validate_ref(package_id, "package_id")
        revision = _validate_ref(revision, "revision")
        actor = _validate_actor(actor)
        reason = _bounded_reason(reason)
        with self._locked():
            state = _read_registry(self.root)
            _require_expected_active(state, expected_active_revision)
            target = _find_record(state, package_id, revision)
            if target is None:
                raise PublishedPackageError("待撤回 revision 不存在")
            if target.get("state") == "revoked":
                return _operation_result(state, target, "already-revoked")
            replacement: dict[str, Any] | None = None
            if target.get("state") == "active":
                replacements = [
                    item
                    for item in state["packages"]
                    if (
                        item is not target
                        and item.get("package_id") == package_id
                        and item.get("state") == "inactive"
                    )
                ]
                replacements.sort(
                    key=lambda item: int(item.get("activation_sequence", 0)), reverse=True
                )
                replacement = replacements[0] if replacements else None
                if replacement is None:
                    raise PublishedPackageError("没有可回退的健康 revision，拒绝撤回当前版本")
                replacement_ref = ActivePackageRef(
                    str(replacement["package_id"]),
                    str(replacement["revision"]),
                    str(replacement["digest"]),
                )
                _load_registered_package(self.root, replacement_ref)
            now = self.clock()
            target["state"] = "revoked"
            target["revoked_at"] = now
            target["revoked_by"] = actor
            target["revoke_reason"] = reason
            if replacement is not None:
                sequence = int(state.get("activation_sequence", 0)) + 1
                state["activation_sequence"] = sequence
                replacement["state"] = "active"
                replacement["activated_at"] = now
                replacement["activated_by"] = actor
                replacement["activation_sequence"] = sequence
                state["active"] = {
                    "package_id": replacement["package_id"],
                    "revision": replacement["revision"],
                    "digest": replacement["digest"],
                }
            _append_event(
                state,
                "revoked",
                target,
                actor=actor,
                timestamp=now,
                reason=reason,
                replacement_revision=(
                    str(replacement["revision"]) if replacement is not None else None
                ),
            )
            self._write_state(state)
            result = _operation_result(state, target, "revoked")
            result["replacement_revision"] = (
                str(replacement["revision"]) if replacement is not None else None
            )
            return result

    def status(self) -> dict[str, Any]:
        state = _read_registry(self.root)
        active = _active_ref(state)
        _load_registered_package(self.root, active)
        return {
            "status": "healthy",
            "active": _ref_dict(active),
            "packages": [dict(item) for item in state["packages"]],
            "event_count": len(state["events"]),
        }

    def _load_or_default(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return {
                "schema_version": 1,
                "active": None,
                "activation_sequence": 0,
                "packages": [],
                "events": [],
            }
        return _read_registry(self.root, require_active=False)

    def _write_state(self, state: dict[str, Any]) -> None:
        payload = (json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
        if len(payload) > _MAX_REGISTRY_BYTES:
            raise PublishedPackageError("公开资料包注册表超过安全上限")
        _atomic_write(self.registry_path, payload, mode=0o644, overwrite=True)

    def _ensure_root(self) -> None:
        if self.root.is_symlink():
            raise PublishedPackageError("公开资料包目录不能是符号链接")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o755)
        if not self.root.is_dir():
            raise PublishedPackageError("公开资料包目录无效")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._ensure_root()
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def _read_registry(root: Path, *, require_active: bool = True) -> dict[str, Any]:
    path = root / "registry.json"
    if path.is_symlink() or not path.is_file():
        raise PublishedPackageError("公开资料包注册表不存在或不是普通文件")
    try:
        payload = json.loads(_read_bounded(path, _MAX_REGISTRY_BYTES, "公开资料包注册表"))
    except json.JSONDecodeError as exc:
        raise PublishedPackageError("公开资料包注册表不是有效 JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise PublishedPackageError("公开资料包注册表版本无效")
    if not isinstance(payload.get("packages"), list) or not isinstance(payload.get("events"), list):
        raise PublishedPackageError("公开资料包注册表结构无效")
    if require_active:
        _active_ref(payload)
    return payload


def _active_ref(state: Mapping[str, Any]) -> ActivePackageRef:
    active = state.get("active")
    if not isinstance(active, dict):
        raise PublishedPackageError("公开资料包尚未激活")
    package_id = _validate_ref(str(active.get("package_id", "")), "package_id")
    revision = _validate_ref(str(active.get("revision", "")), "revision")
    digest = str(active.get("digest", ""))
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise PublishedPackageError("公开资料包 active digest 无效")
    return ActivePackageRef(package_id, revision, digest)


def _active_record(state: dict[str, Any]) -> dict[str, Any] | None:
    active = state.get("active")
    if not isinstance(active, dict):
        return None
    return _find_record(state, str(active.get("package_id")), str(active.get("revision")))


def _find_record(state: Mapping[str, Any], package_id: str, revision: str) -> dict[str, Any] | None:
    packages = state.get("packages")
    if not isinstance(packages, list):
        raise PublishedPackageError("公开资料包注册表结构无效")
    for item in packages:
        if not isinstance(item, dict):
            raise PublishedPackageError("公开资料包 revision 记录无效")
        if item.get("package_id") == package_id and item.get("revision") == revision:
            return item
    return None


def _require_expected_active(state: Mapping[str, Any], expected: str | None) -> None:
    active = state.get("active")
    actual = str(active.get("revision")) if isinstance(active, dict) else None
    if actual != expected:
        raise PublishedPackageError(
            f"active revision 已变化，期望 {expected or 'none'}，实际 {actual or 'none'}"
        )


def _load_registered_package(root: Path, ref: ActivePackageRef) -> PublicPackage:
    path = _package_path(root, ref.package_id, ref.revision)
    if path.is_symlink() or not path.is_file():
        raise PublishedPackageError("active 公开资料包文件不存在或不是普通文件")
    try:
        package = PublicPackage.load(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise PublishedPackageError(f"active 公开资料包校验失败: {exc}") from exc
    if (
        package.package_id != ref.package_id
        or package.revision != ref.revision
        or package.digest != ref.digest
    ):
        raise PublishedPackageError("active 公开资料包与注册表不一致")
    return package


def _package_path(root: Path, package_id: str, revision: str) -> Path:
    package_id = _validate_ref(package_id, "package_id")
    revision = _validate_ref(revision, "revision")
    return root / "packages" / package_id / f"{revision}.json"


def _validate_ref(value: str, field: str) -> str:
    if _SAFE_REF.fullmatch(value) is None:
        raise PublishedPackageError(f"公开资料包 {field} 格式无效")
    return value


def _validate_actor(value: str) -> str:
    if _SAFE_ACTOR.fullmatch(value) is None:
        raise PublishedPackageError("公开资料包操作人格式无效")
    return value


def _bounded_reason(value: str) -> str:
    reason = value.strip()
    if not reason or len(reason) > 240 or any(ord(char) < 32 for char in reason):
        raise PublishedPackageError("撤回原因必须是 1 到 240 个可见字符")
    return reason


def _read_bounded(path: Path, maximum: int, label: str) -> str:
    try:
        if path.stat().st_size > maximum:
            raise PublishedPackageError(f"{label}超过安全上限")
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PublishedPackageError(f"{label}不是 UTF-8") from exc


def _atomic_write(path: Path, payload: bytes, *, mode: int, overwrite: bool) -> None:
    if path.is_symlink():
        raise PublishedPackageError("原子写入目标不能是符号链接")
    if path.exists() and not overwrite:
        raise PublishedPackageError("不可变公开资料包文件已存在")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    descriptor, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw_temp)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _append_event(
    state: dict[str, Any],
    action: str,
    record: Mapping[str, Any],
    *,
    actor: str,
    timestamp: str,
    reason: str | None = None,
    replacement_revision: str | None = None,
) -> None:
    event: dict[str, Any] = {
        "event_id": f"ppe_{secrets.token_hex(8)}",
        "action": action,
        "package_id": record["package_id"],
        "revision": record["revision"],
        "digest": record["digest"],
        "actor": actor,
        "timestamp": timestamp,
    }
    if reason is not None:
        event["reason"] = reason
    if replacement_revision is not None:
        event["replacement_revision"] = replacement_revision
    state["events"].append(event)


def _ref_dict(ref: ActivePackageRef) -> dict[str, str]:
    return {
        "package_id": ref.package_id,
        "revision": ref.revision,
        "digest": ref.digest,
    }


def _operation_result(
    state: Mapping[str, Any], record: Mapping[str, Any], status: str
) -> dict[str, Any]:
    active = state.get("active")
    return {
        "status": status,
        "package_id": record["package_id"],
        "revision": record["revision"],
        "digest": record["digest"],
        "state": record["state"],
        "active_revision": (str(active.get("revision")) if isinstance(active, dict) else None),
    }
