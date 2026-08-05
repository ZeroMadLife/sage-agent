"""Session 级 disposable Git worktree 生命周期。"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from core.coding.context import now

ExecutionWorkspaceKind = Literal["primary", "git_worktree"]
ExecutionWorkspaceStatus = Literal["active", "discarding", "discarded"]
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_GIT_TIMEOUT_SECONDS = 30.0


class ExecutionWorkspaceError(RuntimeError):
    """执行区创建、恢复或释放无法满足安全不变量。"""


@dataclass(frozen=True)
class ExecutionWorkspaceDescriptor:
    """持久化逻辑身份与物理执行根之间的稳定绑定。"""

    schema_version: int
    kind: ExecutionWorkspaceKind
    status: ExecutionWorkspaceStatus
    root: str
    logical_root: str
    source_revision: str
    created_at: str
    discarded_at: str = ""

    def to_dict(self) -> dict[str, object]:
        """转换为 Session JSON 可直接保存的结构。"""
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "status": self.status,
            "root": self.root,
            "logical_root": self.logical_root,
            "source_revision": self.source_revision,
            "created_at": self.created_at,
            "discarded_at": self.discarded_at,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> ExecutionWorkspaceDescriptor:
        """严格解析持久化描述符，未知版本或非法枚举一律拒绝。"""
        if payload.get("schema_version") != 1:
            raise ExecutionWorkspaceError("unsupported execution workspace descriptor")
        kind = str(payload.get("kind", ""))
        status = str(payload.get("status", ""))
        if kind not in {"primary", "git_worktree"} or status not in {
            "active",
            "discarding",
            "discarded",
        }:
            raise ExecutionWorkspaceError("invalid execution workspace descriptor")
        return cls(
            schema_version=1,
            kind=cast(ExecutionWorkspaceKind, kind),
            status=cast(ExecutionWorkspaceStatus, status),
            root=str(payload.get("root", "")),
            logical_root=str(payload.get("logical_root", "")),
            source_revision=str(payload.get("source_revision", "")),
            created_at=str(payload.get("created_at", "")),
            discarded_at=str(payload.get("discarded_at", "")),
        )


class ExecutionWorkspaceManager:
    """在服务端受控目录中创建、恢复和显式丢弃 detached worktree。"""

    def __init__(self, storage_root: Path | str, *, git_binary: str = "git") -> None:
        # 保留路径本身，不能先 resolve 后再把符号链接伪装成普通目录。
        self.storage_root = self._absolute_without_symlinks(Path(storage_root).expanduser())
        self.execution_root = self.storage_root / "execution-workspaces"
        self.git_binary = git_binary

    def primary(self, logical_root: Path | str) -> ExecutionWorkspaceDescriptor:
        """为可信本地模式生成同根描述符，不创建或伪装隔离边界。"""
        logical = Path(logical_root).resolve()
        return ExecutionWorkspaceDescriptor(
            schema_version=1,
            kind="primary",
            status="active",
            root=str(logical),
            logical_root=str(logical),
            source_revision="",
            created_at=now(),
        )

    def create(self, session_id: str, logical_root: Path | str) -> ExecutionWorkspaceDescriptor:
        """从逻辑仓库当前 HEAD 创建干净执行区，忽略主目录未提交改动。"""
        safe_session_id = self._safe_session_id(session_id)
        logical = self._require_repository_root(logical_root)
        self._ensure_execution_root()
        target = self._absolute_without_symlinks(self.execution_root / safe_session_id)
        self._require_expected_target(safe_session_id, target)
        if target.exists() or target.is_symlink():
            raise ExecutionWorkspaceError("execution workspace target already exists")
        source_revision = self._git_output(logical, "rev-parse", "--verify", "HEAD^{commit}")
        if _COMMIT_RE.fullmatch(source_revision) is None:
            raise ExecutionWorkspaceError("logical workspace HEAD is not a commit")

        created = False
        try:
            self._git_output(
                logical,
                "worktree",
                "add",
                "--detach",
                str(target),
                source_revision,
            )
            created = True
            self._git_output(
                logical,
                "worktree",
                "lock",
                "--reason",
                f"sage session={safe_session_id}",
                str(target),
            )
            descriptor = ExecutionWorkspaceDescriptor(
                schema_version=1,
                kind="git_worktree",
                status="active",
                root=str(target),
                logical_root=str(logical),
                source_revision=source_revision,
                created_at=now(),
            )
            self._validate_active(safe_session_id, logical, descriptor)
            return descriptor
        except Exception as exc:
            if created:
                self._remove_worktree(logical, target, best_effort=True)
            if isinstance(exc, ExecutionWorkspaceError):
                raise
            raise ExecutionWorkspaceError("execution workspace creation failed") from exc

    def restore(
        self,
        session_id: str,
        logical_root: Path | str,
        payload: Mapping[str, object],
    ) -> ExecutionWorkspaceDescriptor:
        """校验并复用已有执行区；任何漂移都拒绝回退到主工作区。"""
        safe_session_id = self._safe_session_id(session_id)
        descriptor = ExecutionWorkspaceDescriptor.from_dict(payload)
        if descriptor.status != "active":
            raise ExecutionWorkspaceError("execution workspace is not active")
        raw_logical = Path(logical_root).expanduser()
        if descriptor.kind == "primary":
            logical = raw_logical.resolve()
            if (
                Path(descriptor.logical_root).resolve() != logical
                or Path(descriptor.root).resolve() != logical
            ):
                raise ExecutionWorkspaceError("primary execution workspace descriptor drifted")
            return descriptor
        logical = self._require_repository_root(raw_logical)
        self._validate_active(safe_session_id, logical, descriptor)
        return descriptor

    def discard(
        self,
        session_id: str,
        logical_root: Path | str,
        payload: Mapping[str, object],
    ) -> ExecutionWorkspaceDescriptor:
        """显式移除受控 worktree，但保留 Session、Diff、Test 与 Trace 证据。"""
        safe_session_id = self._safe_session_id(session_id)
        logical = self._require_repository_root(logical_root)
        descriptor = ExecutionWorkspaceDescriptor.from_dict(payload)
        self._validate_descriptor_binding(safe_session_id, logical, descriptor)
        if descriptor.status == "discarded":
            return descriptor
        target = self._absolute_without_symlinks(Path(descriptor.root))
        if target.exists():
            self._validate_active(safe_session_id, logical, descriptor)
        elif descriptor.status == "active":
            raise ExecutionWorkspaceError("execution workspace is unavailable")
        elif not self._worktree_is_registered(logical, target):
            # 上一次请求可能已删除 worktree，却在持久化 discarded 前崩溃。
            return replace(descriptor, status="discarded", discarded_at=now())
        self._remove_worktree(logical, target, best_effort=False)
        return replace(descriptor, status="discarded", discarded_at=now())

    def _validate_descriptor_binding(
        self,
        session_id: str,
        logical_root: Path,
        descriptor: ExecutionWorkspaceDescriptor,
    ) -> None:
        """校验持久化身份和受控路径，不要求 worktree 当前仍然存在。"""
        self._require_managed_storage()
        if descriptor.kind != "git_worktree":
            raise ExecutionWorkspaceError("invalid execution workspace descriptor kind")
        target = Path(descriptor.root)
        self._require_expected_target(session_id, target)
        descriptor_logical = Path(descriptor.logical_root)
        if descriptor_logical.is_symlink() or descriptor_logical.resolve() != logical_root:
            raise ExecutionWorkspaceError("execution workspace descriptor does not match session")
        if _COMMIT_RE.fullmatch(descriptor.source_revision) is None:
            raise ExecutionWorkspaceError("execution workspace descriptor has invalid revision")

    def _validate_active(
        self,
        session_id: str,
        logical_root: Path,
        descriptor: ExecutionWorkspaceDescriptor,
    ) -> None:
        """确认描述符路径、Git 注册和 common dir 都仍绑定到逻辑仓库。"""
        self._validate_descriptor_binding(session_id, logical_root, descriptor)
        target = self._absolute_without_symlinks(Path(descriptor.root))
        if target.is_symlink() or not target.is_dir():
            raise ExecutionWorkspaceError("execution workspace is unavailable")
        git_file = target / ".git"
        if git_file.is_symlink() or not git_file.is_file():
            raise ExecutionWorkspaceError("execution workspace Git metadata is unavailable")
        top_level = Path(self._git_output(target, "rev-parse", "--show-toplevel")).resolve()
        if top_level != target.resolve():
            raise ExecutionWorkspaceError("execution workspace repository root drifted")
        logical_common = self._git_path(logical_root, "--git-common-dir")
        execution_common = self._git_path(target, "--git-common-dir")
        if execution_common != logical_common:
            raise ExecutionWorkspaceError(
                "execution workspace is not registered to logical repository"
            )

    def _remove_worktree(self, logical_root: Path, target: Path, *, best_effort: bool) -> None:
        """只通过 Git 删除已校验目标，失败时不使用递归文件删除兜底。"""
        try:
            self._git_output(logical_root, "worktree", "unlock", str(target), check=False)
            self._git_output(logical_root, "worktree", "remove", "--force", str(target))
            if target.exists() or target.is_symlink():
                raise ExecutionWorkspaceError("execution workspace removal was incomplete")
        except Exception:
            if not best_effort:
                raise ExecutionWorkspaceError("execution workspace discard failed") from None

    def _require_repository_root(self, raw_root: Path | str) -> Path:
        """要求输入恰好是 Git 顶层目录，第一版不隐式映射仓库子目录。"""
        raw = Path(raw_root).expanduser()
        if raw.is_symlink():
            raise ExecutionWorkspaceError("logical workspace is unavailable")
        logical = raw.resolve()
        if not logical.is_dir():
            raise ExecutionWorkspaceError("logical workspace is unavailable")
        try:
            top_level = Path(self._git_output(logical, "rev-parse", "--show-toplevel")).resolve()
        except ExecutionWorkspaceError as exc:
            raise ExecutionWorkspaceError(
                "logical workspace must be a Git repository root"
            ) from exc
        if top_level != logical:
            raise ExecutionWorkspaceError("logical workspace must be a Git repository root")
        return logical

    def _ensure_execution_root(self) -> None:
        """创建服务端受控父目录，并拒绝符号链接替换。"""
        if self.storage_root.is_symlink() or self.execution_root.is_symlink():
            raise ExecutionWorkspaceError("execution workspace storage is unsafe")
        self.storage_root.mkdir(parents=True, exist_ok=True)
        if self.storage_root.is_symlink() or not self.storage_root.is_dir():
            raise ExecutionWorkspaceError("execution workspace storage is unsafe")
        self.execution_root.mkdir(exist_ok=True)
        self._require_managed_storage()

    def _require_managed_storage(self) -> None:
        """每次恢复或销毁前重新确认受控父目录没有被符号链接替换。"""
        if (
            self.storage_root.is_symlink()
            or not self.storage_root.is_dir()
            or self.execution_root.is_symlink()
            or not self.execution_root.is_dir()
        ):
            raise ExecutionWorkspaceError("execution workspace storage is unsafe")

    def _require_expected_target(self, session_id: str, target: Path) -> None:
        """保证后续 Git 删除目标只能是该 Session 的固定子目录。"""
        if target.is_symlink():
            raise ExecutionWorkspaceError("execution workspace target is a symlink")
        expected = self._absolute_without_symlinks(self.execution_root / session_id)
        actual = self._absolute_without_symlinks(target)
        if actual != expected or actual.parent != self.execution_root:
            raise ExecutionWorkspaceError(
                "execution workspace descriptor path is outside managed storage"
            )

    def _worktree_is_registered(self, logical_root: Path, target: Path) -> bool:
        """查询 Git 管理记录，避免把仍有注册信息的缺失目录当作已完成销毁。"""
        output = self._git_output(logical_root, "worktree", "list", "--porcelain", "-z")
        expected = self._absolute_without_symlinks(target)
        for item in output.split("\0"):
            if not item.startswith("worktree "):
                continue
            registered = Path(item.removeprefix("worktree "))
            if self._absolute_without_symlinks(registered) == expected:
                return True
        return False

    def _git_path(self, cwd: Path, argument: str) -> Path:
        """把 Git 返回的绝对或相对元数据路径规范化后再比较。"""
        value = Path(self._git_output(cwd, "rev-parse", argument))
        return (value if value.is_absolute() else cwd / value).resolve()

    def _git_output(self, cwd: Path, *args: str, check: bool = True) -> str:
        """以无 Shell 的参数数组执行 Git，并把外部错误收敛为稳定异常。"""
        try:
            completed = subprocess.run(
                [self.git_binary, "-C", str(cwd), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=_GIT_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExecutionWorkspaceError("Git execution failed") from exc
        if check and completed.returncode != 0:
            raise ExecutionWorkspaceError("Git operation failed")
        return completed.stdout.strip()

    @staticmethod
    def _safe_session_id(session_id: str) -> str:
        """限制目录名字符集，拒绝路径分隔符与点目录。"""
        value = session_id.strip()
        if _SESSION_ID_RE.fullmatch(value) is None or value in {".", ".."}:
            raise ExecutionWorkspaceError("invalid execution workspace session id")
        return value

    @staticmethod
    def _absolute_without_symlinks(path: Path) -> Path:
        """规范化点段和相对路径，但刻意不跟随文件系统符号链接。"""
        return Path(os.path.abspath(path))


def execution_workspace_payload(value: object) -> Mapping[str, object]:
    """从不可信 Session JSON 中提取对象结构，拒绝列表或标量。"""
    if not isinstance(value, Mapping):
        raise ExecutionWorkspaceError("persisted session is missing execution workspace")
    return cast(Mapping[str, object], value)


__all__ = [
    "ExecutionWorkspaceDescriptor",
    "ExecutionWorkspaceError",
    "ExecutionWorkspaceKind",
    "ExecutionWorkspaceManager",
    "ExecutionWorkspaceStatus",
    "execution_workspace_payload",
]
