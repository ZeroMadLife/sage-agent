"""Native local sandbox contract tests."""

from __future__ import annotations

import asyncio
import shlex
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from sage_harness import SandboxPolicyError

from core.coding.context import WorkspaceContext
from core.harness.native_local_sandbox import NativeLocalSandbox

_MACOS_SEATBELT_AVAILABLE = sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file()


def _roots(tmp_path: Path) -> tuple[Path, Path, WorkspaceContext]:
    """创建彼此分离的 logical root 与 disposable execution root。"""
    logical = tmp_path / "primary"
    execution = tmp_path / "execution"
    logical.mkdir()
    execution.mkdir()
    return logical, execution, WorkspaceContext(execution, role="disposable")


def test_native_local_requires_disposable_execution_root(tmp_path: Path) -> None:
    """native_local 不能直接绑定 primary workspace。"""
    with pytest.raises(SandboxPolicyError, match="disposable execution workspace"):
        NativeLocalSandbox(
            WorkspaceContext(tmp_path),
            thread_id="native-primary",
            backend="seatbelt",
            sandbox_binary="/usr/bin/sandbox-exec",
        )


def test_native_local_requires_logical_root_for_deny_rules(tmp_path: Path) -> None:
    """缺失 logical root 时不能生成保护 primary 的策略。"""
    execution = tmp_path / "execution"
    execution.mkdir()
    with pytest.raises(SandboxPolicyError, match="logical workspace root"):
        NativeLocalSandbox(
            WorkspaceContext(execution, role="disposable"),
            thread_id="native-no-logical-root",
            backend="seatbelt",
            sandbox_binary="/usr/bin/sandbox-exec",
        )


def test_native_local_allows_nested_execution_root_but_not_primary_root(
    tmp_path: Path,
) -> None:
    """受控的嵌套 worktree 可以复用默认 .coding 布局，但不能绑定 primary 本身。"""
    logical = tmp_path / "primary"
    execution = logical / ".coding" / "execution-workspaces" / "session"
    execution.mkdir(parents=True)

    sandbox = NativeLocalSandbox(
        WorkspaceContext(execution, role="disposable"),
        thread_id="native-nested",
        logical_workspace_root=logical,
        backend="seatbelt",
        sandbox_binary="/usr/bin/sandbox-exec",
    )

    assert sandbox.descriptor.capabilities.isolated is True


@pytest.mark.skipif(not _MACOS_SEATBELT_AVAILABLE, reason="requires macOS Seatbelt")
def test_native_seatbelt_nested_worktree_can_write_execution_but_not_primary(
    tmp_path: Path,
) -> None:
    """默认嵌套布局只重新放行 worktree，primary 其余文件仍不可读写。"""
    logical = tmp_path / "primary"
    execution = logical / ".coding" / "execution-workspaces" / "session"
    execution.mkdir(parents=True)
    primary_file = logical / "primary.txt"
    primary_file.write_text("primary-private-content\n", encoding="utf-8")
    sandbox = NativeLocalSandbox(
        WorkspaceContext(execution, role="disposable"),
        thread_id="native-nested-live",
        logical_workspace_root=logical,
        backend="seatbelt",
        sandbox_binary="/usr/bin/sandbox-exec",
    )

    execution_result = asyncio.run(
        sandbox.invoke("run_shell", {"command": "printf execution-ok > nested.txt"})
    )
    primary_result = asyncio.run(sandbox.invoke("run_shell", {"command": f"cat {primary_file}"}))

    assert execution_result.is_error is False
    assert (execution / "nested.txt").read_text(encoding="utf-8") == "execution-ok"
    assert primary_result.is_error is True
    assert "primary-private-content" not in primary_result.content


def test_native_local_descriptor_isolated_and_host_free(tmp_path: Path) -> None:
    """描述符必须声明隔离且不具备宿主访问能力。"""
    logical, _, execution = _roots(tmp_path)
    sandbox = NativeLocalSandbox(
        execution,
        thread_id="native-descriptor",
        logical_workspace_root=logical,
        backend="seatbelt",
        sandbox_binary="/usr/bin/sandbox-exec",
    )

    assert sandbox.descriptor.provider == "native_local"
    assert sandbox.descriptor.capabilities.isolated is True
    assert sandbox.descriptor.capabilities.host_access is False
    assert str(logical) not in repr(sandbox.descriptor)


def test_native_file_tools_write_only_execution_root(tmp_path: Path) -> None:
    """文件工具写入只能落在 execution root。"""
    logical, _, execution = _roots(tmp_path)

    async def run() -> tuple[object, object]:
        sandbox = NativeLocalSandbox(
            execution,
            thread_id="native-file",
            logical_workspace_root=logical,
            backend="seatbelt",
            sandbox_binary="/usr/bin/sandbox-exec",
        )
        wrote = await sandbox.invoke(
            "write_file",
            {"path": "notes/example.txt", "content": "isolated\n"},
        )
        await sandbox.aclose()
        return wrote, execution.root / "notes/example.txt"

    wrote, execution_file = asyncio.run(run())

    assert wrote.is_error is False
    assert execution_file.read_text(encoding="utf-8") == "isolated\n"
    assert not (logical / "notes/example.txt").exists()


def test_native_seatbelt_command_has_no_host_shell_fallback(tmp_path: Path) -> None:
    """macOS 命令必须经过 sandbox-exec，禁止普通宿主 Shell 回退。"""
    logical, _, execution = _roots(tmp_path)
    sandbox = NativeLocalSandbox(
        execution,
        thread_id="native-shell",
        logical_workspace_root=logical,
        backend="seatbelt",
        sandbox_binary="/usr/bin/sandbox-exec",
    )

    command = sandbox._shell_command("printf native-ok")

    assert command[:3] == ["/usr/bin/sandbox-exec", "-p", command[2]]
    assert "sandbox-exec" in command[0]
    assert command[-3:] == ["/bin/sh", "-lc", "printf native-ok"]
    profile = sandbox._seatbelt_profile()
    assert "(deny default)" in profile
    assert '(import "system.sb")' in profile
    assert "(deny network*)" in profile
    assert '(deny file-write* (subpath "/"))' in profile
    assert "(allow signal (target same-sandbox))" in profile
    assert "(allow signal)" not in profile
    assert str(logical) in profile


def test_native_missing_backend_fails_closed(tmp_path: Path) -> None:
    """原生后端缺失时必须显式失败。"""
    _, _, execution = _roots(tmp_path)
    with pytest.raises(SandboxPolicyError, match="requires sandbox-exec"):
        NativeLocalSandbox(
            execution,
            thread_id="native-missing",
            logical_workspace_root=tmp_path / "primary",
            backend="seatbelt",
            sandbox_binary="",
        )


def test_native_missing_runtime_binary_returns_tool_error(tmp_path: Path) -> None:
    """原生后端在运行时消失时返回结构化错误，不执行宿主 Shell。"""
    logical, _, execution = _roots(tmp_path)
    sandbox = NativeLocalSandbox(
        execution,
        thread_id="native-runtime-missing",
        logical_workspace_root=logical,
        backend="seatbelt",
        sandbox_binary="/tmp/sage-sandbox-executable-does-not-exist",
    )

    result = asyncio.run(sandbox.invoke("run_shell", {"command": "printf should-not-run"}))

    assert result.is_error is True
    assert result.error_code == "native_sandbox_unavailable"
    assert "should-not-run" not in result.content


def test_native_bubblewrap_command_is_unshared_and_workspace_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Linux 命令必须 unshare 全部 namespace 并只挂载 execution root。"""
    logical, execution_path, execution = _roots(tmp_path)
    runtime = tmp_path / "runtime"
    runtime_bin = runtime / "bin"
    runtime_bin.mkdir(parents=True)
    monkeypatch.setenv("VIRTUAL_ENV", str(runtime))
    monkeypatch.setenv("PATH", f"{runtime_bin}:/usr/bin:/bin")
    sandbox = NativeLocalSandbox(
        execution,
        thread_id="native-bwrap",
        logical_workspace_root=logical,
        backend="bubblewrap",
        sandbox_binary="/usr/bin/bwrap",
    )

    command = sandbox._shell_command("printf linux-ok")

    assert command[0] == "/usr/bin/bwrap"
    assert "--unshare-all" in command
    assert "--unshare-net" in command
    assert ["--bind", str(execution_path), "/workspace"] == command[
        command.index("--bind") : command.index("--bind") + 3
    ]
    assert "/workspace" in command
    assert str(execution_path) not in command[command.index("--bind") + 2 :]
    assert str(logical) not in command
    assert not any(
        command[index : index + 3] == ["--ro-bind", "/run", "/run"]
        for index in range(len(command) - 2)
    )
    path_index = command.index("PATH")
    assert str(runtime) not in command[path_index + 1]
    assert "/sage-runtime/" in command[path_index + 1]
    assert command[-3:] == ["/bin/sh", "-lc", "printf linux-ok"]


@pytest.mark.skipif(not _MACOS_SEATBELT_AVAILABLE, reason="requires macOS Seatbelt")
def test_native_shell_can_execute_when_backend_is_available(tmp_path: Path) -> None:
    """可用 Seatbelt 后端仍允许安全范围内的 Shell 执行。"""
    logical, _, execution = _roots(tmp_path)
    (logical / "secret.txt").write_text("primary-secret\n", encoding="utf-8")
    sandbox = NativeLocalSandbox(
        execution,
        thread_id="native-live-shell",
        logical_workspace_root=logical,
        backend="seatbelt",
        sandbox_binary="/usr/bin/sandbox-exec",
    )

    result = asyncio.run(sandbox.invoke("run_shell", {"command": "printf native-ok"}))

    assert result.is_error is False
    assert "native-ok" in result.content


def test_native_read_only_mode_disables_file_tools_and_uses_read_only_mount(
    tmp_path: Path,
) -> None:
    """只读模式关闭文件工具写入，并让 Linux execution mount 使用 ro-bind。"""
    logical, execution_path, execution = _roots(tmp_path)
    sandbox = NativeLocalSandbox(
        execution,
        thread_id="native-read-only",
        logical_workspace_root=logical,
        backend="bubblewrap",
        sandbox_binary="/usr/bin/bwrap",
        allow_writes=False,
    )

    assert sandbox.descriptor.capabilities.write_files is False
    with pytest.raises(SandboxPolicyError, match="file writes are disabled"):
        asyncio.run(
            sandbox.invoke(
                "write_file",
                {"path": "no.txt", "content": "blocked\n"},
            )
        )
    command = sandbox._shell_command("printf blocked > no.txt")
    execution_index = command.index(str(execution_path))

    assert command[execution_index - 1 : execution_index + 2] == [
        "--ro-bind",
        str(execution_path),
        "/workspace",
    ]
    assert not (execution.root / "no.txt").exists()


@pytest.mark.skipif(not _MACOS_SEATBELT_AVAILABLE, reason="requires macOS Seatbelt")
def test_native_seatbelt_read_only_mode_blocks_shell_writes(tmp_path: Path) -> None:
    """Seatbelt 只读模式不能通过 Shell 修改 execution root。"""
    logical, _, execution = _roots(tmp_path)
    sandbox = NativeLocalSandbox(
        execution,
        thread_id="native-seatbelt-read-only",
        logical_workspace_root=logical,
        backend="seatbelt",
        sandbox_binary="/usr/bin/sandbox-exec",
        allow_writes=False,
    )

    result = asyncio.run(sandbox.invoke("run_shell", {"command": "printf blocked > no.txt"}))

    assert result.is_error is True
    assert not (execution.root / "no.txt").exists()


@pytest.mark.skipif(not _MACOS_SEATBELT_AVAILABLE, reason="requires macOS Seatbelt")
def test_native_seatbelt_blocks_loopback_network(tmp_path: Path) -> None:
    """Seatbelt 连本机监听端口也必须被网络策略拒绝。"""
    logical, _, execution = _roots(tmp_path)
    sandbox = NativeLocalSandbox(
        execution,
        thread_id="native-seatbelt-network",
        logical_workspace_root=logical,
        backend="seatbelt",
        sandbox_binary="/usr/bin/sandbox-exec",
    )
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(0.2)
    port = listener.getsockname()[1]
    script = f'import socket; socket.create_connection(("127.0.0.1", {port}), 1)'
    command = f"/usr/bin/python3 -c {shlex.quote(script)}"

    try:
        result = asyncio.run(sandbox.invoke("run_shell", {"command": command}))
        with pytest.raises(TimeoutError):
            listener.accept()
    finally:
        listener.close()

    assert result.is_error is True


@pytest.mark.skipif(not _MACOS_SEATBELT_AVAILABLE, reason="requires macOS Seatbelt")
def test_native_seatbelt_cannot_signal_host_process(tmp_path: Path) -> None:
    """Seatbelt Shell 只能向同一沙盒内的进程发送信号。"""
    logical, _, execution = _roots(tmp_path)
    sandbox = NativeLocalSandbox(
        execution,
        thread_id="native-seatbelt-signal",
        logical_workspace_root=logical,
        backend="seatbelt",
        sandbox_binary="/usr/bin/sandbox-exec",
    )
    host_process = subprocess.Popen(["/bin/sleep", "10"])

    try:
        result = asyncio.run(
            sandbox.invoke("run_shell", {"command": f"kill -TERM {host_process.pid}"})
        )
        assert result.is_error is True
        assert host_process.poll() is None
    finally:
        host_process.terminate()
        host_process.wait(timeout=2)


@pytest.mark.skipif(not _MACOS_SEATBELT_AVAILABLE, reason="requires macOS Seatbelt")
def test_native_shell_cannot_read_or_write_primary(tmp_path: Path) -> None:
    """原生 Shell 不能读取或写入 logical primary workspace。"""
    logical, _, execution = _roots(tmp_path)
    primary_file = logical / "secret.txt"
    primary_file.write_text("primary-secret\n", encoding="utf-8")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("outside-private-content\n", encoding="utf-8")
    sandbox = NativeLocalSandbox(
        execution,
        thread_id="native-primary-boundary",
        logical_workspace_root=logical,
        backend="seatbelt",
        sandbox_binary="/usr/bin/sandbox-exec",
    )

    read_result = asyncio.run(sandbox.invoke("run_shell", {"command": f"cat {primary_file}"}))
    write_result = asyncio.run(
        sandbox.invoke("run_shell", {"command": f"printf changed > {primary_file}"})
    )
    outside_result = asyncio.run(sandbox.invoke("run_shell", {"command": f"cat {outside_file}"}))

    assert read_result.is_error is True
    assert "primary-secret" not in read_result.content
    assert write_result.is_error is True
    assert outside_result.is_error is True
    assert "outside-private-content" not in outside_result.content
    assert primary_file.read_text(encoding="utf-8") == "primary-secret\n"
