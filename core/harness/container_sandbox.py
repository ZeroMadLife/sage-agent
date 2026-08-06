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

from core.coding.context import (
    IGNORED_PATH_NAMES,
    PROTECTED_PATH_NAMES,
    WorkspaceContext,
    clip,
)
from core.coding.memory import workspace_id_from_path

_ALL_OPERATIONS = frozenset(
    {"list_files", "read_file", "search", "write_file", "patch_file", "run_shell"}
)
_WRITE_OPERATIONS = frozenset({"write_file", "patch_file"})
_DEFAULT_IMAGE = "python:3.11-slim"
_CONTAINER_ROOT = "/workspace"
_DEFAULT_COMMAND_TIMEOUT = 30.0
_SECURITY_PROFILE = "level1-v2"
_MEMORY_BYTES = 1024 * 1024 * 1024
_NANO_CPUS = 2_000_000_000
_PIDS_LIMIT = 256
_TMPFS_OPTIONS = "rw,noexec,nosuid,nodev,size=64m"
_IGNORED_NAMES = frozenset({*IGNORED_PATH_NAMES, *PROTECTED_PATH_NAMES, ".env", ".env.*"})


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
        workspace_id: str | None = None,
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
        self._workspace_id = (workspace_id or workspace_id_from_path(workspace.root)).strip()
        if not self._workspace_id:
            raise ValueError("workspace_id must not be empty")
        self._closed = False
        self._started = False
        self._lifecycle_lock = threading.RLock()
        self._container_name = self._name(self._workspace_id, normalized_thread)
        thread_digest = hashlib.sha256(normalized_thread.encode()).hexdigest()[:12]
        self._descriptor = SandboxDescriptor(
            sandbox_id=f"container:{self._workspace_id}:{thread_digest}",
            provider="container",
            workspace_id=self._workspace_id,
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
        try:
            result = await asyncio.to_thread(self._invoke_sync, operation, dict(arguments))
        except ValueError as exc:
            result = (str(exc), True)
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
    def discard_owned(
        cls,
        workspace: WorkspaceContext,
        *,
        thread_id: str,
        workspace_id: str | None = None,
        image: str = _DEFAULT_IMAGE,
        docker_binary: str = "docker",
    ) -> int:
        """显式释放一个 Session 的容器，只允许删除 Sage 自己拥有的实例。"""
        sandbox = cls(
            workspace,
            thread_id=thread_id,
            workspace_id=workspace_id,
            image=image,
            docker_binary=docker_binary,
        )
        sandbox._require_isolated_daemon()
        existing = sandbox._docker_capture(
            ["ps", "-aq", "--filter", f"name=^{sandbox._container_name}$"],
            check=False,
        ).strip()
        if not existing:
            return 0
        payload = sandbox._inspect_container()
        sandbox._require_owned_container(payload)
        sandbox._docker_capture(["rm", "-f", sandbox._container_name])
        return 1

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
            self._require_isolated_daemon()
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
                payload = self._inspect_container()
                self._require_owned_container(payload)
                violations = self._security_violations(payload)
                if not violations:
                    self._started = True
                    return
                self._docker_capture(["rm", "-f", self._container_name])

            stopped = self._docker_capture(
                ["ps", "-aq", "--filter", f"name=^{self._container_name}$"],
                check=False,
            ).strip()
            if stopped:
                payload = self._inspect_container()
                self._require_owned_container(payload)
                self._docker_capture(["rm", "-f", self._container_name])

            self._workspace.root.mkdir(parents=True, exist_ok=True)
            self._docker_capture(self._container_run_args())
            payload = self._inspect_container()
            violations = self._security_violations(payload)
            if violations:
                self._docker_capture(["rm", "-f", self._container_name], check=False)
                joined = ", ".join(violations)
                raise SandboxPolicyError(f"container security profile mismatch: {joined}")
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
        violations = self._security_violations(payload)
        return {
            "sandbox_id": self._descriptor.sandbox_id,
            "provider": self._descriptor.provider,
            "status": status,
            "running": running,
            "healthy": running and not violations,
            "image": image,
            "security_profile": _SECURITY_PROFILE,
            "security_violations": violations,
        }

    def _container_run_args(self) -> list[str]:
        mount = (
            f"type=bind,source={self._workspace.root},target={_CONTAINER_ROOT},"
            "readonly=false,bind-propagation=rprivate"
        )
        return [
            "run",
            "-d",
            "--name",
            self._container_name,
            "--label",
            "com.sage.sandbox=true",
            "--label",
            f"com.sage.sandbox_id={self._descriptor.sandbox_id}",
            "--label",
            f"com.sage.security_profile={_SECURITY_PROFILE}",
            "--network",
            "none",
            "--pids-limit",
            str(_PIDS_LIMIT),
            "--memory",
            "1g",
            "--memory-swap",
            "1g",
            "--cpus",
            "2",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges=true",
            "--ulimit",
            "nofile=256:256",
            "--ulimit",
            "nproc=256:256",
            "--ulimit",
            "fsize=67108864:67108864",
            "--init",
            "--stop-timeout",
            "3",
            "--tmpfs",
            f"/tmp:{_TMPFS_OPTIONS}",
            "--mount",
            mount,
            self._image,
            "sh",
            "-c",
            "mkdir -p /tmp/sage-home && exec sleep infinity",
        ]

    def _inspect_container(self) -> dict[str, Any]:
        raw = self._docker_capture(["inspect", "--format", "{{json .}}", self._container_name])
        try:
            payload: Any = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise SandboxPolicyError("container inspect returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise SandboxPolicyError("container inspect returned invalid payload")
        return payload

    def _require_owned_container(self, payload: dict[str, Any]) -> None:
        labels = payload.get("Config", {}).get("Labels", {})
        if not isinstance(labels, dict) or labels.get("com.sage.sandbox") != "true":
            raise SandboxPolicyError("sandbox container name is owned by another workload")
        if labels.get("com.sage.sandbox_id") != self._descriptor.sandbox_id:
            raise SandboxPolicyError("sandbox container ownership label does not match")

    def _security_violations(self, payload: dict[str, Any]) -> list[str]:
        violations: list[str] = []
        config = payload.get("Config", {})
        host = payload.get("HostConfig", {})
        state = payload.get("State", {})
        if (
            not isinstance(config, dict)
            or not isinstance(host, dict)
            or not isinstance(state, dict)
        ):
            return ["invalid_inspect_payload"]
        labels = config.get("Labels", {})
        if (
            not isinstance(labels, dict)
            or labels.get("com.sage.security_profile") != _SECURITY_PROFILE
        ):
            violations.append("security_profile")
        if config.get("Image") != self._image:
            violations.append("image_reference")
        if host.get("NetworkMode") != "none":
            violations.append("network")
        if host.get("ReadonlyRootfs") is not True:
            violations.append("read_only_rootfs")
        if host.get("PidsLimit") != _PIDS_LIMIT:
            violations.append("pids_limit")
        if host.get("Memory") != _MEMORY_BYTES or host.get("MemorySwap") != _MEMORY_BYTES:
            violations.append("memory_limit")
        if host.get("NanoCpus") != _NANO_CPUS:
            violations.append("cpu_limit")
        cap_drop = host.get("CapDrop", [])
        if not isinstance(cap_drop, list) or "ALL" not in cap_drop:
            violations.append("cap_drop")
        security_options = host.get("SecurityOpt", [])
        if not isinstance(security_options, list) or not any(
            str(item).startswith("no-new-privileges") for item in security_options
        ):
            violations.append("no_new_privileges")
        if any("seccomp=unconfined" in str(item) for item in security_options):
            violations.append("seccomp_unconfined")
        if host.get("Init") is not True:
            violations.append("init")
        tmpfs = host.get("Tmpfs", {})
        options = str(tmpfs.get("/tmp", "")) if isinstance(tmpfs, dict) else ""
        required_tmpfs = {"rw", "noexec", "nosuid", "nodev", "size=64m"}
        if not required_tmpfs.issubset(set(options.split(","))):
            violations.append("tmpfs")
        ulimits = host.get("Ulimits", [])
        actual_ulimits = (
            {
                str(item.get("Name")): (item.get("Soft"), item.get("Hard"))
                for item in ulimits
                if isinstance(item, dict)
            }
            if isinstance(ulimits, list)
            else {}
        )
        expected_ulimits = {
            "nofile": (256, 256),
            "nproc": (256, 256),
            "fsize": (67_108_864, 67_108_864),
        }
        if any(actual_ulimits.get(name) != value for name, value in expected_ulimits.items()):
            violations.append("ulimits")
        mounts = payload.get("Mounts", [])
        expected_source = str(self._workspace.root.resolve())
        if not isinstance(mounts, list) or len(mounts) != 1:
            violations.append("mount_count")
        else:
            mount = mounts[0]
            if not isinstance(mount, dict) or (
                mount.get("Type") != "bind"
                or mount.get("Source") != expected_source
                or mount.get("Destination") != _CONTAINER_ROOT
                or mount.get("RW") is not True
                or mount.get("Propagation") != "rprivate"
            ):
                violations.append("workspace_mount")
        if state.get("Running") is not True:
            violations.append("not_running")
        return violations

    def _require_isolated_daemon(self) -> None:
        raw = self._docker_capture(["info", "--format", "{{json .}}"])
        try:
            payload: Any = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise SandboxPolicyError("docker daemon security metadata is invalid") from exc
        if not isinstance(payload, dict):
            raise SandboxPolicyError("docker daemon security metadata is invalid")
        security_options = payload.get("SecurityOptions", [])
        operating_system = str(payload.get("OperatingSystem", ""))
        has_seccomp = any("seccomp" in str(item) for item in security_options)
        isolated_host = any("rootless" in str(item) for item in security_options) or (
            "Docker Desktop" in operating_system
        )
        if not has_seccomp or not isolated_host:
            raise SandboxPolicyError(
                "container sandbox requires rootless Docker or Docker Desktop with seccomp"
            )

    def _invoke_sync(
        self, operation: SandboxOperation, arguments: dict[str, Any]
    ) -> tuple[str, bool]:
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
                f"{number:>4}: {line}" for number, line in enumerate(lines, start=start)
            )
            return clip(f"# {self._relative_path(str(arguments['path']))}\n{numbered}"), False

        if operation == "search":
            path = self._virtual_path(str(arguments.get("path", ".")))
            pattern = str(arguments["pattern"])
            excluded_names = [
                item
                for name in sorted(PROTECTED_PATH_NAMES)
                for item in (f"--exclude={name}", f"--exclude-dir={name}")
            ]
            excluded_dirs = [f"--exclude-dir={name}" for name in sorted(IGNORED_PATH_NAMES)]
            output = self._docker_capture(
                [
                    "exec",
                    "--workdir",
                    _CONTAINER_ROOT,
                    self._container_name,
                    "grep",
                    "-r",
                    "-n",
                    "-I",
                    "--exclude=.env",
                    "--exclude=.env.*",
                    *excluded_names,
                    *excluded_dirs,
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
            return (
                f"wrote {self._relative_path(str(arguments['path']))} ({len(content)} chars)",
                False,
            )

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
            f"timeout --signal=KILL {max(timeout, 1.0):g}s " f"sh -lc {shlex.quote(command)}"
        )
        command_args = [
            "exec",
            "--workdir",
            _CONTAINER_ROOT,
        ]
        for key, value in env.items():
            command_args.extend(["--env", f"{key}={value}"])
        command_args.extend([self._container_name, "sh", "-lc", timeout_command])
        completed = self._docker_run(command_args, timeout=timeout + 5, check=False)
        output = completed.stdout.strip()
        if completed.stderr.strip():
            output = (
                f"{output}\nstderr:\n{completed.stderr.strip()}"
                if output
                else completed.stderr.strip()
            )
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
    def _name(workspace_id: str, thread_id: str) -> str:
        digest = hashlib.sha256(f"{workspace_id}:{thread_id}".encode()).hexdigest()[:20]
        return f"sage-sandbox-{digest}"

    @staticmethod
    def _safe_environment() -> dict[str, str]:
        # Never pass host virtualenv paths or credentials into the container.
        # The image owns its executable search path and home directory.
        return {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "HOME": "/tmp/sage-home",
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
