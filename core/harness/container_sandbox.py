"""Docker-backed isolated sandbox adapter for Harness coding runs.

The adapter intentionally uses the Docker CLI instead of importing a Docker
SDK.  This keeps the provider optional for local development while preserving
the same small ``SandboxPort`` contract used by ``LocalWorkspaceSandbox``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sage_harness import (
    SandboxCapabilities,
    SandboxDescriptor,
    SandboxOperation,
    SandboxPolicyError,
    SandboxResult,
)

from core.coding.context import WorkspaceContext, clip
from core.coding.memory import workspace_id_from_path

_ALL_OPERATIONS = frozenset(
    {"list_files", "read_file", "search", "write_file", "patch_file", "run_shell"}
)
_WRITE_OPERATIONS = frozenset({"write_file", "patch_file"})
_DEFAULT_IMAGE = "python:3.11-slim"
_CONTAINER_ROOT = "/workspace"
_DEFAULT_COMMAND_TIMEOUT = 30.0
_IGNORED_NAMES = frozenset(
    {
        ".git",
        ".coding",
        ".pico",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".venv",
        "venv",
        "node_modules",
    }
)


class ContainerWorkspaceSandbox:
    """Run coding workspace operations inside one Docker container.

    The host workspace is the only writable bind mount.  Containers run with
    no network, bounded resources, and a read-only root filesystem.  The
    provider does not grant approval: callers still invoke it only after
    Sage's ToolExecutor policy and approval gates have completed.
    """

    def __init__(
        self,
        workspace: WorkspaceContext,
        *,
        thread_id: str,
        image: str = _DEFAULT_IMAGE,
        allow_host_shell: bool = True,
        allow_writes: bool = True,
        docker_binary: str = "docker",
    ) -> None:
        normalized_thread = thread_id.strip()
        normalized_image = image.strip()
        if not normalized_thread:
            raise ValueError("thread_id must not be empty")
        if not normalized_image:
            raise ValueError("container image must not be empty")
        self._workspace = workspace
        self._image = normalized_image
        self._docker = docker_binary
        self._closed = False
        self._started = False
        self._lifecycle_lock = threading.RLock()
        self._container_name = self._name(workspace, normalized_thread)
        workspace_id = workspace_id_from_path(workspace.root)
        thread_digest = hashlib.sha256(normalized_thread.encode()).hexdigest()[:12]
        self._descriptor = SandboxDescriptor(
            sandbox_id=f"container:{workspace_id}:{thread_digest}",
            provider="container",
            workspace_id=workspace_id,
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
        return self._descriptor

    async def invoke(
        self,
        operation: SandboxOperation,
        arguments: Mapping[str, object],
    ) -> SandboxResult:
        """Execute one already-authorized operation inside the container."""
        if self._closed:
            raise SandboxPolicyError("sandbox is closed")
        if operation not in _ALL_OPERATIONS:
            raise SandboxPolicyError(f"unsupported sandbox operation: {operation}")
        if operation in _WRITE_OPERATIONS and not self._descriptor.capabilities.write_files:
            raise SandboxPolicyError("sandbox file writes are disabled")
        if operation == "run_shell" and not self._descriptor.capabilities.shell:
            raise SandboxPolicyError("container shell is disabled by provider policy")

        await asyncio.to_thread(self._ensure_started)
        result = await asyncio.to_thread(self._invoke_sync, operation, dict(arguments))
        return SandboxResult(
            operation=operation,
            content=result[0],
            is_error=result[1],
            metadata={
                "sandbox_id": self._descriptor.sandbox_id,
                "workspace_id": self._descriptor.workspace_id,
                "provider": self._descriptor.provider,
                "container_name": self._container_name,
            },
        )

    async def aclose(self) -> None:
        """Stop and remove this provider-owned container, best effort."""
        if self._closed:
            return
        self._closed = True
        if self._started:
            await asyncio.to_thread(self._stop_container)

    async def health(self) -> dict[str, object]:
        """Return a sanitized provider health snapshot for diagnostics."""
        return await asyncio.to_thread(self._health_sync)

    @classmethod
    def reconcile_stopped(cls, *, docker_binary: str = "docker") -> int:
        """Remove Sage-owned containers that are no longer running.

        Running containers are intentionally left alone: they may belong to a
        live process or another API instance. A later session acquire will
        inspect and reuse the deterministic container name. Only terminal
        ``created``, ``exited`` and ``dead`` entries are safe to remove at
        process startup.
        """
        removed = 0
        try:
            for status in ("created", "exited", "dead"):
                completed = subprocess.run(
                    [
                        docker_binary,
                        "ps",
                        "-aq",
                        "--filter",
                        "label=com.sage.sandbox=true",
                        "--filter",
                        f"status={status}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                if completed.returncode != 0:
                    raise SandboxPolicyError(cls._docker_error(completed))
                for container_id in completed.stdout.splitlines():
                    container_id = container_id.strip()
                    if not container_id:
                        continue
                    removed_result = subprocess.run(
                        [docker_binary, "rm", "-f", container_id],
                        capture_output=True,
                        text=True,
                        timeout=60,
                        check=False,
                    )
                    if removed_result.returncode == 0:
                        removed += 1
        except FileNotFoundError as exc:
            raise SandboxPolicyError("docker executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxPolicyError("sandbox reconciliation timed out") from exc
        return removed

    def _ensure_started(self) -> None:
        with self._lifecycle_lock:
            if self._started:
                return
            existing = self._docker_capture(
                [
                    "ps",
                    "-q",
                    "--filter",
                    f"name=^{self._container_name}$",
                    "--filter",
                    "status=running",
                ],
                check=False,
            ).strip()
            if existing:
                self._started = True
                return

            stopped = self._docker_capture(
                ["ps", "-aq", "--filter", f"name=^{self._container_name}$"],
                check=False,
            ).strip()
            if stopped:
                self._docker_capture(["rm", "-f", self._container_name], check=False)

            self._workspace.root.mkdir(parents=True, exist_ok=True)
            self._docker_capture(
                [
                    "run",
                    "-d",
                    "--name",
                    self._container_name,
                    "--label",
                    "com.sage.sandbox=true",
                    "--label",
                    f"com.sage.sandbox_id={self._descriptor.sandbox_id}",
                    "--network",
                    "none",
                    "--pids-limit",
                    "256",
                    "--memory",
                    "1g",
                    "--cpus",
                    "2",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=64m",
                    "--mount",
                    f"type=bind,source={self._workspace.root},target={_CONTAINER_ROOT},readonly=false",
                    self._image,
                    "sleep",
                    "infinity",
                ]
            )
            self._started = True

    def _health_sync(self) -> dict[str, object]:
        try:
            completed = self._docker_run(
                ["inspect", "--format", "{{json .}}", self._container_name],
                timeout=60.0,
                check=False,
            )
        except SandboxPolicyError as exc:
            return {
                "sandbox_id": self._descriptor.sandbox_id,
                "provider": self._descriptor.provider,
                "status": "unavailable",
                "running": False,
                "healthy": False,
                "error": str(exc),
            }
        if completed.returncode != 0:
            return {
                "sandbox_id": self._descriptor.sandbox_id,
                "provider": self._descriptor.provider,
                "status": "missing",
                "running": False,
                "healthy": False,
            }
        try:
            payload = json.loads(completed.stdout)
            state = payload.get("State", {})
            status = str(state.get("Status", "unknown"))
            running = bool(state.get("Running", False))
            image = str(payload.get("Config", {}).get("Image", ""))
        except (TypeError, ValueError, AttributeError):
            return {
                "sandbox_id": self._descriptor.sandbox_id,
                "provider": self._descriptor.provider,
                "status": "invalid",
                "running": False,
                "healthy": False,
            }
        return {
            "sandbox_id": self._descriptor.sandbox_id,
            "provider": self._descriptor.provider,
            "status": status,
            "running": running,
            "healthy": running and image == self._image,
            "image": image,
        }

    def _invoke_sync(self, operation: SandboxOperation, arguments: dict[str, Any]) -> tuple[str, bool]:
        if operation == "list_files":
            path = self._virtual_path(str(arguments.get("path", ".")))
            output = self._docker_capture(
                [
                    "exec",
                    "--workdir",
                    _CONTAINER_ROOT,
                    self._container_name,
                    "find",
                    path,
                    "-mindepth",
                    "1",
                    "-maxdepth",
                    "1",
                    *[item for name in _IGNORED_NAMES for item in ("!", "-name", name)],
                    "-print",
                ]
            )
            entries: list[str] = []
            for line in output.splitlines()[:200]:
                entry = line.strip()
                if not entry:
                    continue
                stat = self._docker_capture(
                    [
                        "exec",
                        self._container_name,
                        "sh",
                        "-c",
                        f"if [ -d {shlex.quote(entry)} ]; then printf d; else printf f; fi",
                    ]
                ).strip()
                marker = "[D]" if stat == "d" else "[F]"
                entries.append(f"{marker} {self._rewrite_paths(entry)}")
            return "\n".join(entries) or "(empty)", False

        if operation == "read_file":
            path = self._virtual_path(str(arguments["path"]))
            start = int(arguments.get("start", 1))
            end = int(arguments.get("end", 200))
            output = self._docker_capture(
                [
                    "exec",
                    "--workdir",
                    _CONTAINER_ROOT,
                    self._container_name,
                    "sed",
                    "-n",
                    f"{start},{end}p",
                    path,
                ]
            )
            lines = output.splitlines()
            numbered = "\n".join(
                f"{number:>4}: {line}"
                for number, line in enumerate(lines, start=start)
            )
            return clip(f"# {self._relative_path(str(arguments['path']))}\n{numbered}"), False

        if operation == "search":
            path = self._virtual_path(str(arguments.get("path", ".")))
            pattern = str(arguments["pattern"])
            output = self._docker_capture(
                [
                    "exec",
                    "--workdir",
                    _CONTAINER_ROOT,
                    self._container_name,
                    "grep",
                    "-R",
                    "-n",
                    "-I",
                    "--",
                    pattern,
                    path,
                ],
                check=False,
            )
            return self._rewrite_paths(output) or "(no matches)", False

        if operation == "write_file":
            path = self._virtual_path(str(arguments["path"]))
            content = str(arguments["content"])
            self._docker_exec_stdin(
                [
                    "exec",
                    "-i",
                    "--workdir",
                    _CONTAINER_ROOT,
                    self._container_name,
                    "sh",
                    "-c",
                    f"mkdir -p {shlex.quote(str(Path(path).parent))} && cat > {shlex.quote(path)}",
                ],
                content.encode(),
            )
            return f"wrote {self._relative_path(str(arguments['path']))} ({len(content)} chars)", False

        if operation == "patch_file":
            payload = json.dumps(
                {
                    "path": self._virtual_path(str(arguments["path"])),
                    "old_text": str(arguments["old_text"]),
                    "new_text": str(arguments["new_text"]),
                },
                ensure_ascii=False,
            ).encode()
            script = (
                "import json,sys; p=json.load(sys.stdin); "
                "text=open(p['path'],encoding='utf-8').read(); count=text.count(p['old_text']); "
                "assert count == 1, f'old_text must occur exactly once, found {count}'; "
                "open(p['path'],'w',encoding='utf-8').write(text.replace(p['old_text'],p['new_text'],1))"
            )
            self._docker_exec_stdin(
                [
                    "exec",
                    "-i",
                    "--workdir",
                    _CONTAINER_ROOT,
                    self._container_name,
                    "python",
                    "-c",
                    script,
                ],
                payload,
            )
            return f"patched {self._relative_path(str(arguments['path']))}", False

        command = str(arguments["command"])
        timeout = float(arguments.get("timeout", _DEFAULT_COMMAND_TIMEOUT))
        env = self._safe_environment()
        timeout_command = (
            f"timeout --signal=KILL {max(timeout, 1.0):g}s "
            f"sh -lc {shlex.quote(command)}"
        )
        command_args = [
            "exec",
            "--workdir",
            _CONTAINER_ROOT,
        ]
        for key, value in env.items():
            command_args.extend(["--env", f"{key}={value}"])
        command_args.extend([self._container_name, "sh", "-lc", timeout_command])
        completed = self._docker_run(command_args, timeout=timeout + 5)
        output = completed.stdout.strip()
        if completed.stderr.strip():
            output = f"{output}\nstderr:\n{completed.stderr.strip()}" if output else completed.stderr.strip()
        output = output or "(empty)"
        return clip(f"exit_code: {completed.returncode}\n{output}"), completed.returncode != 0

    def _stop_container(self) -> None:
        with self._lifecycle_lock:
            self._docker_capture(["rm", "-f", self._container_name], check=False)
            self._started = False

    def _virtual_path(self, raw_path: str) -> str:
        resolved = self._workspace.path(raw_path)
        relative = resolved.relative_to(self._workspace.root)
        return str(Path(_CONTAINER_ROOT) / relative) if str(relative) != "." else _CONTAINER_ROOT

    def _relative_path(self, raw_path: str) -> str:
        return str(self._workspace.path(raw_path).relative_to(self._workspace.root))

    @staticmethod
    def _rewrite_paths(content: str) -> str:
        return content.replace(f"{_CONTAINER_ROOT}/", "").replace(_CONTAINER_ROOT, ".")

    @staticmethod
    def _name(workspace: WorkspaceContext, thread_id: str) -> str:
        digest = hashlib.sha256(f"{workspace.root}:{thread_id}".encode()).hexdigest()[:20]
        return f"sage-sandbox-{digest}"

    @staticmethod
    def _safe_environment() -> dict[str, str]:
        # Never pass host virtualenv paths or credentials into the container.
        # The image owns its executable search path and home directory.
        return {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": "/root",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }

    def _docker_capture(self, args: list[str], *, check: bool = True) -> str:
        completed = self._docker_run(args, timeout=60.0, check=check)
        return completed.stdout

    def _docker_exec_stdin(self, args: list[str], payload: bytes) -> None:
        completed = self._docker_run(args, timeout=60.0, input_data=payload)
        if completed.returncode != 0:
            raise SandboxPolicyError(self._docker_error(completed))

    def _docker_run(
        self,
        args: list[str],
        *,
        timeout: float,
        check: bool = True,
        input_data: bytes | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                [self._docker, *args],
                input=input_data,
                capture_output=True,
                text=input_data is None,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SandboxPolicyError("docker executable is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise SandboxPolicyError(f"container operation timed out after {timeout:g}s") from exc
        if check and completed.returncode != 0:
            raise SandboxPolicyError(self._docker_error(completed))
        return completed

    @staticmethod
    def _docker_error(completed: subprocess.CompletedProcess[str]) -> str:
        detail = (completed.stderr or completed.stdout or "docker operation failed").strip()
        return clip(f"container operation failed: {detail}", limit=1000)


__all__ = ["ContainerWorkspaceSandbox"]
