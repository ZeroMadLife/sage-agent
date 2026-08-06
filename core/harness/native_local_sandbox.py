"""macOS Seatbelt/Linux bubblewrap-backed local sandbox adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Literal

from sage_harness import (
    SandboxCapabilities,
    SandboxDescriptor,
    SandboxOperation,
    SandboxPolicyError,
    SandboxResult,
)

from core.coding.context import WorkspaceContext, clip
from core.coding.memory import workspace_id_from_path
from core.coding.tools.registry import execute_tool

_READ_OPERATIONS = frozenset({"list_files", "read_file", "search"})
_WRITE_OPERATIONS = frozenset({"write_file", "patch_file"})
_ALL_OPERATIONS = _READ_OPERATIONS | _WRITE_OPERATIONS | {"run_shell"}
_LOCAL_ENVIRONMENTS = frozenset({"development", "test"})
_BUBBLEWRAP_WORKSPACE_ROOT = Path("/workspace")
_BUBBLEWRAP_RUNTIME_ROOT = Path("/sage-runtime")
_SHELL_ENV_KEYS = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PYTHONPATH",
        "CONDA_DEFAULT_ENV",
        "CONDA_PREFIX",
        "VIRTUAL_ENV",
    }
)
NativeSandboxBackend = Literal["seatbelt", "bubblewrap"]


class NativeLocalSandbox:
    """在 disposable execution workspace 上提供原生 OS 级 Shell 隔离。

    文件工具仍复用 Sage 自己的路径校验，但只能接收 disposable 根目录；Shell
    子进程再由 macOS Seatbelt 或 Linux bubblewrap 限制读写和网络。这个适配器
    不负责 Permission、Policy 或 Approval，也不会把失败静默降级成 Host Shell。
    """

    def __init__(
        self,
        workspace: WorkspaceContext,
        *,
        thread_id: str,
        app_env: str = "development",
        allow_host_shell: bool = True,
        allow_writes: bool = True,
        workspace_id: str | None = None,
        logical_workspace_root: Path | str | None = None,
        backend: NativeSandboxBackend | None = None,
        sandbox_binary: str | None = None,
    ) -> None:
        """初始化原生沙盒，并拒绝把 primary workspace 当作隔离根。"""
        environment = app_env.strip().lower()
        if environment not in _LOCAL_ENVIRONMENTS:
            raise SandboxPolicyError(
                "NativeLocalSandbox is limited to development/test; use container in deployment"
            )
        if workspace.role != "disposable":
            raise SandboxPolicyError("NativeLocalSandbox requires a disposable execution workspace")
        normalized_thread = thread_id.strip()
        if not normalized_thread:
            raise ValueError("thread_id must not be empty")
        execution_root = workspace.root.resolve()
        logical_root: Path | None = None
        if logical_workspace_root is not None:
            logical_root = Path(logical_workspace_root).resolve()
            if execution_root == logical_root or logical_root.is_relative_to(execution_root):
                raise SandboxPolicyError(
                    "NativeLocalSandbox execution root must not contain logical workspace"
                )
        else:
            raise SandboxPolicyError(
                "NativeLocalSandbox requires the logical workspace root for deny rules"
            )
        resolved_backend, resolved_binary = self._resolve_backend(backend, sandbox_binary)
        stable_workspace_id = (workspace_id or workspace_id_from_path(execution_root)).strip()
        if not stable_workspace_id:
            raise ValueError("workspace_id must not be empty")

        self._workspace = workspace
        self._backend = resolved_backend
        self._sandbox_binary = resolved_binary
        self._allow_writes = allow_writes
        self._logical_workspace_root = logical_root
        self._closed = False
        self._temp_root = Path(tempfile.mkdtemp(prefix="sage-native-sandbox-"))
        thread_digest = hashlib.sha256(normalized_thread.encode()).hexdigest()[:12]
        self._descriptor = SandboxDescriptor(
            sandbox_id=f"native_local:{stable_workspace_id}:{thread_digest}",
            provider="native_local",
            workspace_id=stable_workspace_id,
            capabilities=SandboxCapabilities(
                isolated=True,
                host_access=False,
                read_files=True,
                write_files=allow_writes,
                shell=allow_host_shell,
            ),
        )

    @property
    def descriptor(self) -> SandboxDescriptor:
        """返回不暴露宿主路径的原生沙盒描述符。"""
        return self._descriptor

    async def invoke(
        self,
        operation: SandboxOperation,
        arguments: Mapping[str, object],
    ) -> SandboxResult:
        """执行已经通过 Sage 授权的文件或 Shell 操作。"""
        if self._closed:
            raise SandboxPolicyError("sandbox is closed")
        if operation not in _ALL_OPERATIONS:
            raise SandboxPolicyError(f"unsupported sandbox operation: {operation}")
        capabilities = self._descriptor.capabilities
        if operation in _READ_OPERATIONS and not capabilities.read_files:
            raise SandboxPolicyError("sandbox file reads are disabled")
        if operation in _WRITE_OPERATIONS and not capabilities.write_files:
            raise SandboxPolicyError("sandbox file writes are disabled")
        if operation == "run_shell" and not capabilities.shell:
            raise SandboxPolicyError("native shell is disabled by provider policy")

        if operation == "run_shell":
            content, is_error, error_code, retryable = await asyncio.to_thread(
                self._run_shell,
                dict(arguments),
            )
        else:
            result = await asyncio.to_thread(
                execute_tool,
                self._workspace,
                operation,
                dict(arguments),
            )
            content, is_error, error_code, retryable = (
                result.content,
                result.is_error,
                result.error_code,
                result.retryable,
            )
        return SandboxResult(
            operation=operation,
            content=content,
            is_error=is_error,
            error_code=error_code,
            retryable=retryable,
            metadata={
                "sandbox_id": self._descriptor.sandbox_id,
                "workspace_id": self._descriptor.workspace_id,
                "provider": self._descriptor.provider,
                "backend": self._backend,
            },
        )

    async def aclose(self) -> None:
        """关闭本次 Shell 临时目录，不删除 Session execution workspace。"""
        if self._closed:
            return
        self._closed = True
        await asyncio.to_thread(shutil.rmtree, self._temp_root, True)

    @staticmethod
    def _resolve_backend(
        backend: NativeSandboxBackend | None,
        sandbox_binary: str | None,
    ) -> tuple[NativeSandboxBackend, str]:
        """按操作系统选择原生后端，找不到强制 fail closed。"""
        if backend is None:
            if sys.platform == "darwin":
                backend = "seatbelt"
            elif sys.platform.startswith("linux"):
                backend = "bubblewrap"
            else:
                raise SandboxPolicyError("native local sandbox is unsupported on this OS")
        expected_binary = "sandbox-exec" if backend == "seatbelt" else "bwrap"
        binary = shutil.which(expected_binary) if sandbox_binary is None else sandbox_binary
        if not binary:
            raise SandboxPolicyError(f"native local sandbox requires {expected_binary}")
        return backend, binary

    def _run_shell(
        self,
        arguments: dict[str, object],
    ) -> tuple[str, bool, str | None, bool | None]:
        """用 Seatbelt 或 bubblewrap 启动 Shell，禁止网络和 primary 读写。"""
        command = str(arguments.get("command", ""))
        try:
            requested_timeout = int(str(arguments.get("timeout", 20)))
        except (TypeError, ValueError):
            requested_timeout = 20
        timeout = max(1, min(requested_timeout, 300))
        try:
            process = subprocess.Popen(
                self._shell_command(command),
                cwd=self._workspace.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._shell_environment(),
                start_new_session=True,
            )
        except FileNotFoundError:
            return (
                "native sandbox executable is unavailable",
                True,
                "native_sandbox_unavailable",
                False,
            )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            stdout, stderr = self._terminate_process_group(process)
            content = (
                f"command timed out after {timeout}s\n"
                "failure_kind: timeout\n"
                "retryable: true\n"
                f"timeout_seconds: {timeout}\n"
                f"stdout:\n{stdout.strip() or '(empty)'}\n"
                f"stderr:\n{stderr.strip() or '(empty)'}"
            )
            return clip(content), True, "shell_timeout", True
        content = (
            f"exit_code: {process.returncode}\n"
            f"stdout:\n{stdout.strip() or '(empty)'}\n"
            f"stderr:\n{stderr.strip() or '(empty)'}"
        )
        return (
            clip(content),
            process.returncode != 0,
            "shell_exit_nonzero" if process.returncode != 0 else None,
            False if process.returncode != 0 else None,
        )

    def _shell_command(self, command: str) -> list[str]:
        """构造可审计的 OS 沙盒命令，不经过宿主 Shell 拼接。"""
        if self._backend == "seatbelt":
            return [
                self._sandbox_binary,
                "-p",
                self._seatbelt_profile(),
                "/bin/sh",
                "-lc",
                command,
            ]
        return [
            self._sandbox_binary,
            *self._bubblewrap_args(),
            "/bin/sh",
            "-lc",
            command,
        ]

    def _seatbelt_profile(self) -> str:
        """生成默认拒绝、仅放行 execution root 和运行时依赖的 Seatbelt 策略。"""
        read_roots = self._runtime_read_roots()
        lines = [
            "(version 1)",
            "(deny default)",
            '(import "system.sb")',
            "(deny network*)",
            '(deny file-write* (subpath "/"))',
            f"(deny file-read* (subpath {json.dumps(str(Path.home()))}))",
        ]
        if self._logical_workspace_root is not None:
            lines.append(
                f"(deny file-read* (subpath {json.dumps(str(self._logical_workspace_root))}))"
            )
            lines.append(
                f"(deny file-write* (subpath {json.dumps(str(self._logical_workspace_root))}))"
            )
        lines.extend(
            [
                "(allow process-exec)",
                "(allow process-fork)",
                "(allow signal (target same-sandbox))",
                "(allow sysctl-read)",
            ]
        )
        for root in read_roots:
            lines.append(f"(allow file-read* (subpath {json.dumps(str(root))}))")
        lines.append(f"(allow file-read* (subpath {json.dumps(str(self._workspace.root))}))")
        lines.append(f"(allow file-read* (subpath {json.dumps(str(self._temp_root))}))")
        if self._allow_writes:
            lines.append(f"(allow file-write* (subpath {json.dumps(str(self._workspace.root))}))")
        lines.append(f"(allow file-write* (subpath {json.dumps(str(self._temp_root))}))")
        return "\n".join(lines)

    def _runtime_read_roots(self) -> tuple[Path, ...]:
        """收集 Shell 必需的只读运行时目录，避免放行整个宿主 HOME。"""
        roots: list[Path] = []
        defaults = (
            "/usr",
            "/bin",
            "/sbin",
            "/System",
            "/Library",
            "/private/etc",
            "/private/var/db",
            "/private/var/select",
            "/dev",
        )
        for raw in (*defaults, *os.environ.get("PATH", "").split(":"), sys.prefix):
            if not raw:
                continue
            path = Path(raw)
            if path.is_dir() and path not in roots:
                roots.append(path)
        virtual_env = os.environ.get("VIRTUAL_ENV", "")
        if virtual_env and Path(virtual_env).is_dir() and Path(virtual_env) not in roots:
            roots.append(Path(virtual_env))
        return tuple(roots)

    def _bubblewrap_args(self) -> list[str]:
        """构造 Linux bubblewrap 的只读系统根、可写执行根和禁网参数。"""
        args = [
            "--die-with-parent",
            "--unshare-all",
            "--unshare-net",
            "--new-session",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--clearenv",
        ]
        bound_roots: list[Path] = []
        for raw in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"):
            path = Path(raw)
            if path.exists():
                args.extend(["--ro-bind", str(path), str(path)])
                bound_roots.append(path)
        runtime_args, runtime_path = self._bubblewrap_runtime_mounts(
            tuple(Path(raw) for raw in bound_roots)
        )
        args.extend(runtime_args)
        mount_mode = "--bind" if self._allow_writes else "--ro-bind"
        args.extend(["--dir", str(_BUBBLEWRAP_WORKSPACE_ROOT)])
        args.extend(
            [
                mount_mode,
                str(self._workspace.root),
                str(_BUBBLEWRAP_WORKSPACE_ROOT),
            ]
        )
        args.extend(["--setenv", "HOME", "/tmp", "--setenv", "TMPDIR", "/tmp"])
        args.extend(["--setenv", "PATH", runtime_path])
        args.extend(["--chdir", str(_BUBBLEWRAP_WORKSPACE_ROOT)])
        return args

    def _bubblewrap_runtime_mounts(
        self,
        system_roots: tuple[Path, ...],
    ) -> tuple[list[str], str]:
        """把宿主运行时映射到只读别名，避免暴露其父目录树。"""
        args = ["--dir", str(_BUBBLEWRAP_RUNTIME_ROOT)]
        path_entries: list[str] = []
        aliases: list[tuple[Path, Path]] = []

        def covered_by(path: Path, roots: tuple[Path, ...]) -> bool:
            """判断一个运行时路径是否已由只读系统 mount 覆盖。"""
            return any(path == root or path.is_relative_to(root) for root in roots)

        runtime_candidates = (sys.prefix, os.environ.get("VIRTUAL_ENV", ""))
        for raw in runtime_candidates:
            if not raw:
                continue
            source = Path(raw)
            if not source.is_dir() or covered_by(source, system_roots):
                continue
            if any(source == existing for existing, _ in aliases):
                continue
            target = _BUBBLEWRAP_RUNTIME_ROOT / f"root-{len(aliases)}"
            args.extend(["--dir", str(target), "--ro-bind", str(source), str(target)])
            aliases.append((source, target))

        for raw in os.environ.get("PATH", "/usr/bin:/bin").split(":"):
            if not raw:
                continue
            source = Path(raw)
            if not source.is_dir():
                continue
            if source == self._workspace.root or source.is_relative_to(self._workspace.root):
                relative = source.relative_to(self._workspace.root)
                path_entries.append(str(_BUBBLEWRAP_WORKSPACE_ROOT / relative))
                continue
            if covered_by(source, system_roots):
                path_entries.append(str(source))
                continue
            alias = next(
                (
                    target / source.relative_to(root)
                    for root, target in aliases
                    if source == root or source.is_relative_to(root)
                ),
                None,
            )
            if alias is None:
                alias = _BUBBLEWRAP_RUNTIME_ROOT / f"path-{len(path_entries)}"
                args.extend(["--dir", str(alias), "--ro-bind", str(source), str(alias)])
            path_entries.append(str(alias))

        if not path_entries:
            path_entries.extend(("/usr/bin", "/bin"))
        return args, ":".join(dict.fromkeys(path_entries))

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[str]) -> tuple[str, str]:
        """先终止进程组，再在超时后强制结束残留子进程。"""
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            return process.communicate(timeout=0.5)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            return process.communicate()

    def _shell_environment(self) -> dict[str, str]:
        """只向沙盒 Shell 传递必要环境，并把 HOME/TMPDIR 指向临时区。"""
        environment = {key: value for key, value in os.environ.items() if key in _SHELL_ENV_KEYS}
        environment["HOME"] = str(self._temp_root)
        environment["TMPDIR"] = str(self._temp_root)
        environment.setdefault("PATH", "/usr/bin:/bin")
        return environment


__all__ = ["NativeLocalSandbox", "NativeSandboxBackend"]
