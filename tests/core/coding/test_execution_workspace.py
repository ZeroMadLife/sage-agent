"""Disposable execution workspace lifecycle tests."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from core.coding.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)
from core.coding.memory import workspace_id_from_path
from core.coding.runtime import CodingRuntime


def _git(repo: Path, *args: str) -> str:
    """在测试仓库执行 Git，并返回去除首尾空白后的输出。"""
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _git_repo(root: Path) -> Path:
    """创建带初始提交的最小真实仓库，避免用 mock 掩盖 worktree 契约。"""
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Sage Tests")
    _git(root, "config", "user.email", "sage-tests@example.invalid")
    (root / ".gitignore").write_text(".coding/\n", encoding="utf-8")
    (root / "tracked.txt").write_text("committed\n", encoding="utf-8")
    _git(root, "add", ".gitignore", "tracked.txt")
    _git(root, "commit", "-qm", "initial")
    return root


def test_create_uses_clean_detached_head_without_copying_primary_changes(tmp_path: Path) -> None:
    """新执行区固定到当前 HEAD，主工作区脏改动不能静默渗入。"""
    repo = _git_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("dirty primary\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("primary only\n", encoding="utf-8")
    manager = ExecutionWorkspaceManager(repo / ".coding")

    descriptor = manager.create("session-1", repo)

    assert descriptor.kind == "git_worktree"
    assert descriptor.status == "active"
    assert descriptor.logical_root == str(repo.resolve())
    assert descriptor.source_revision == _git(repo, "rev-parse", "HEAD")
    execution_root = Path(descriptor.root)
    assert execution_root == (repo / ".coding" / "execution-workspaces" / "session-1").resolve()
    assert (execution_root / "tracked.txt").read_text(encoding="utf-8") == "committed\n"
    assert not (execution_root / "untracked.txt").exists()
    assert _git(execution_root, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert (repo / "tracked.txt").read_text(encoding="utf-8") == "dirty primary\n"


def test_restore_reuses_existing_worktree_and_preserves_session_changes(tmp_path: Path) -> None:
    """跨 run 或进程恢复必须复用原执行区，不能从 HEAD 重新创建。"""
    repo = _git_repo(tmp_path / "repo")
    manager = ExecutionWorkspaceManager(repo / ".coding")
    descriptor = manager.create("session-2", repo)
    execution_root = Path(descriptor.root)
    (execution_root / "session-change.txt").write_text("keep me\n", encoding="utf-8")

    restored = manager.restore("session-2", repo, descriptor.to_dict())

    assert restored == descriptor
    assert (Path(restored.root) / "session-change.txt").read_text(encoding="utf-8") == "keep me\n"


def test_restore_fails_closed_for_tampered_or_missing_execution_root(tmp_path: Path) -> None:
    """持久化路径被替换或执行区丢失时，不得回退到主工作区。"""
    repo = _git_repo(tmp_path / "repo")
    manager = ExecutionWorkspaceManager(repo / ".coding")
    descriptor = manager.create("session-3", repo)
    tampered = descriptor.to_dict()
    tampered["root"] = str(tmp_path / "outside")

    with pytest.raises(ExecutionWorkspaceError, match="descriptor"):
        manager.restore("session-3", repo, tampered)

    _git(repo, "worktree", "unlock", descriptor.root)
    _git(repo, "worktree", "remove", "--force", descriptor.root)
    with pytest.raises(ExecutionWorkspaceError, match="unavailable"):
        manager.restore("session-3", repo, descriptor.to_dict())


def test_explicit_discard_removes_worktree_but_preserves_evidence(tmp_path: Path) -> None:
    """显式丢弃只释放执行区，已经保存的 Diff/Test/Trace 继续可审计。"""
    repo = _git_repo(tmp_path / "repo")
    storage_root = repo / ".coding"
    manager = ExecutionWorkspaceManager(storage_root)
    descriptor = manager.create("session-4", repo)
    evidence = storage_root / "evidence" / "session-4" / "run-1" / "diff.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("{}\n", encoding="utf-8")
    (Path(descriptor.root) / "dirty.txt").write_text("discard me\n", encoding="utf-8")

    discarded = manager.discard("session-4", repo, descriptor.to_dict())

    assert discarded.status == "discarded"
    assert not Path(descriptor.root).exists()
    assert evidence.read_text(encoding="utf-8") == "{}\n"
    assert descriptor.root not in _git(repo, "worktree", "list", "--porcelain")


def test_discard_finishes_after_external_cleanup_during_discarding(tmp_path: Path) -> None:
    """崩溃恢复时若 Git 注册和目标目录都已消失，discard 可以安全幂等收口。"""
    repo = _git_repo(tmp_path / "repo")
    manager = ExecutionWorkspaceManager(repo / ".coding")
    descriptor = manager.create("session-4-retry", repo)
    discarding = replace(descriptor, status="discarding")

    _git(repo, "worktree", "unlock", descriptor.root)
    _git(repo, "worktree", "remove", "--force", descriptor.root)

    discarded = manager.discard("session-4-retry", repo, discarding.to_dict())

    assert discarded.status == "discarded"
    assert not Path(descriptor.root).exists()
    assert descriptor.root not in _git(repo, "worktree", "list", "--porcelain")


def test_discard_reconciles_missing_directory_with_stale_git_registration(tmp_path: Path) -> None:
    """目标目录先消失但 Git 注册仍在时，discard 必须完成注册清理而不是误报成功。"""
    repo = _git_repo(tmp_path / "repo")
    manager = ExecutionWorkspaceManager(repo / ".coding")
    descriptor = manager.create("session-stale-registration", repo)
    discarding = replace(descriptor, status="discarding")
    _git(repo, "worktree", "unlock", descriptor.root)
    shutil.rmtree(descriptor.root)

    discarded = manager.discard("session-stale-registration", repo, discarding.to_dict())

    assert discarded.status == "discarded"
    assert descriptor.root not in _git(repo, "worktree", "list", "--porcelain")


def test_restore_rejects_execution_parent_symlink_replacement(tmp_path: Path) -> None:
    """执行区父目录被替换后，恢复必须拒绝跟随符号链接。"""
    repo = _git_repo(tmp_path / "repo")
    storage_root = repo / ".coding"
    manager = ExecutionWorkspaceManager(storage_root)
    descriptor = manager.create("session-parent-link", repo)
    execution_parent = storage_root / "execution-workspaces"
    backup_parent = storage_root / "execution-workspaces-backup"
    execution_parent.rename(backup_parent)
    execution_parent.symlink_to(backup_parent, target_is_directory=True)

    try:
        with pytest.raises(ExecutionWorkspaceError, match="storage|symlink"):
            manager.restore("session-parent-link", repo, descriptor.to_dict())
    finally:
        execution_parent.unlink()
        backup_parent.rename(execution_parent)
        manager.discard("session-parent-link", repo, descriptor.to_dict())


def test_create_fails_closed_when_logical_workspace_is_not_git_root(tmp_path: Path) -> None:
    """SEC-02 第一版仅接受仓库根目录，避免子目录映射产生身份歧义。"""
    repo = _git_repo(tmp_path / "repo")
    subdir = repo / "src"
    subdir.mkdir()
    manager = ExecutionWorkspaceManager(repo / ".coding")

    with pytest.raises(ExecutionWorkspaceError, match="Git repository root"):
        manager.create("session-5", subdir)


def test_primary_descriptor_keeps_lazy_local_workspace_behavior(tmp_path: Path) -> None:
    """可信 local_workspace 仍允许 Runtime 先建立、工作目录后按需创建。"""
    workspace = tmp_path / "created-later"
    manager = ExecutionWorkspaceManager(tmp_path / ".coding")

    descriptor = manager.primary(workspace)
    restored = manager.restore("local-session", workspace, descriptor.to_dict())

    assert restored == descriptor
    assert descriptor.kind == "primary"
    assert not workspace.exists()


def test_runtime_separates_logical_identity_from_disposable_execution(tmp_path: Path) -> None:
    """工具与 Diff 使用 worktree，Memory 和 workspace_id 继续使用逻辑根。"""
    repo = _git_repo(tmp_path / "repo")
    descriptor = ExecutionWorkspaceManager(repo / ".coding").create("session-6", repo)
    runtime = CodingRuntime(
        session_id="session-6",
        workspace_root=repo,
        execution_workspace=descriptor,
        model=object(),
        storage_root=tmp_path / "state",
    )

    assert runtime.logical_workspace.root == repo.resolve()
    assert runtime.execution_workspace.root == Path(descriptor.root)
    assert runtime.workspace is runtime.execution_workspace
    assert runtime.execution_workspace.role == "disposable"
    assert runtime.diff_tracker.workspace_root == Path(descriptor.root)
    assert runtime.plan_mode.workspace_root == Path(descriptor.root)
    assert runtime.skill_registry.root == Path(descriptor.root)
    assert runtime.session["workspace_root"] == str(repo.resolve())
    assert runtime.session["execution_workspace"] == descriptor.to_dict()
    expected_workspace_id = workspace_id_from_path(repo)
    assert runtime.memory_manager.durable.root.name == expected_workspace_id


def test_runtime_resume_validates_persisted_execution_root(tmp_path: Path) -> None:
    """直接从 Runtime 恢复也必须复用同一 worktree 校验，不能绕过 API 安全边界。"""
    repo = _git_repo(tmp_path / "repo")
    storage_root = repo / ".coding"
    descriptor = ExecutionWorkspaceManager(storage_root).create("session-resume", repo)
    CodingRuntime(
        session_id="session-resume",
        workspace_root=repo,
        execution_workspace=descriptor,
        model=object(),
        storage_root=storage_root,
    )

    _git(repo, "worktree", "unlock", descriptor.root)
    _git(repo, "worktree", "remove", "--force", descriptor.root)

    with pytest.raises(ExecutionWorkspaceError, match="unavailable"):
        CodingRuntime.resume("session-resume", object(), storage_root)
