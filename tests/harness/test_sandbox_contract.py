"""Contract tests for the application-neutral and local sandbox boundary."""

from __future__ import annotations

import asyncio
import copy
import json
import subprocess
from pathlib import Path

import pytest
from sage_harness import SandboxCapabilities, SandboxPolicyError

from core.coding.context import WorkspaceContext
from core.harness.container_sandbox import ContainerWorkspaceSandbox
from core.harness.local_sandbox import LocalWorkspaceSandbox


def test_descriptor_is_scoped_without_exposing_the_host_workspace(tmp_path: Path) -> None:
    first = LocalWorkspaceSandbox(WorkspaceContext(tmp_path), thread_id="thread-a")
    second = LocalWorkspaceSandbox(WorkspaceContext(tmp_path), thread_id="thread-b")

    assert first.descriptor.sandbox_id != second.descriptor.sandbox_id
    assert first.descriptor.provider == "local_workspace"
    assert first.descriptor.capabilities == SandboxCapabilities(
        isolated=False,
        host_access=True,
        read_files=True,
        write_files=True,
        shell=False,
    )
    assert str(tmp_path) not in repr(first.descriptor)


def test_local_sandbox_reuses_workspace_path_validation_and_file_tools(tmp_path: Path) -> None:
    async def run() -> tuple[object, object, object]:
        workspace = WorkspaceContext(tmp_path)
        sandbox = LocalWorkspaceSandbox(workspace, thread_id="thread-a")
        wrote = await sandbox.invoke(
            "write_file",
            {"path": "notes/example.txt", "content": "alpha\nbeta\n"},
        )
        read = await sandbox.invoke(
            "read_file",
            {"path": "notes/example.txt", "start": 1, "end": 2},
        )
        searched = await sandbox.invoke(
            "search",
            {"path": "notes", "pattern": "beta"},
        )
        await sandbox.aclose()
        return wrote, read, searched

    wrote, read, searched = asyncio.run(run())

    assert wrote.is_error is False
    assert "notes/example.txt" in wrote.content
    assert "1: alpha" in read.content
    assert "notes/example.txt:2:beta" in searched.content


def test_local_sandbox_rejects_workspace_escape(tmp_path: Path) -> None:
    async def run() -> object:
        sandbox = LocalWorkspaceSandbox(WorkspaceContext(tmp_path), thread_id="thread-a")
        return await sandbox.invoke("read_file", {"path": "../secret.txt"})

    result = asyncio.run(run())

    assert result.is_error is True
    assert "escapes workspace root" in result.content


@pytest.mark.parametrize("app_env", ["production", "staging", "preview"])
def test_local_sandbox_is_disabled_outside_trusted_local_environments(
    tmp_path: Path,
    app_env: str,
) -> None:
    with pytest.raises(SandboxPolicyError, match="isolated container"):
        LocalWorkspaceSandbox(
            WorkspaceContext(tmp_path),
            thread_id="thread-a",
            app_env=app_env,
        )


def test_host_shell_and_file_writes_are_independent_capabilities(tmp_path: Path) -> None:
    async def run() -> tuple[object, object]:
        readonly = LocalWorkspaceSandbox(
            WorkspaceContext(tmp_path),
            thread_id="thread-readonly",
            allow_writes=False,
        )
        with pytest.raises(SandboxPolicyError, match="writes are disabled"):
            await readonly.invoke("write_file", {"path": "blocked.txt", "content": "x"})
        with pytest.raises(SandboxPolicyError, match="host shell is disabled"):
            await readonly.invoke("run_shell", {"command": "printf blocked"})

        shell = LocalWorkspaceSandbox(
            WorkspaceContext(tmp_path),
            thread_id="thread-shell",
            allow_host_shell=True,
            allow_writes=False,
        )
        result = await shell.invoke("run_shell", {"command": "printf sandbox-ok"})
        await shell.aclose()
        return readonly.descriptor, result

    descriptor, result = asyncio.run(run())

    assert descriptor.capabilities.write_files is False
    assert descriptor.capabilities.shell is False
    assert result.is_error is False
    assert "sandbox-ok" in result.content


def test_closed_local_sandbox_rejects_new_operations(tmp_path: Path) -> None:
    async def run() -> None:
        sandbox = LocalWorkspaceSandbox(WorkspaceContext(tmp_path), thread_id="thread-a")
        await sandbox.aclose()
        with pytest.raises(SandboxPolicyError, match="closed"):
            await sandbox.invoke("list_files", {"path": "."})

    asyncio.run(run())


def test_isolated_capability_cannot_also_claim_host_access() -> None:
    with pytest.raises(ValueError, match="isolated sandbox"):
        SandboxCapabilities(isolated=True, host_access=True)


def test_container_health_reports_missing_without_leaking_host_path(tmp_path: Path) -> None:
    async def run() -> object:
        sandbox = ContainerWorkspaceSandbox(
            WorkspaceContext(tmp_path),
            thread_id="health-missing",
            docker_binary="sage-docker-does-not-exist",
        )
        return await sandbox.health()

    health = asyncio.run(run())

    assert health["status"] == "unavailable"
    assert health["healthy"] is False
    assert str(tmp_path) not in repr(health)


def _hardened_inspect_payload(sandbox: ContainerWorkspaceSandbox, root: Path) -> dict[str, object]:
    return {
        "Config": {
            "Image": "python:3.11-slim",
            "Labels": {
                "com.sage.sandbox": "true",
                "com.sage.sandbox_id": sandbox.descriptor.sandbox_id,
                "com.sage.security_profile": "level1-v2",
            },
        },
        "HostConfig": {
            "NetworkMode": "none",
            "ReadonlyRootfs": True,
            "PidsLimit": 256,
            "Memory": 1024 * 1024 * 1024,
            "MemorySwap": 1024 * 1024 * 1024,
            "NanoCpus": 2_000_000_000,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "Init": True,
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,nodev,size=64m"},
            "Ulimits": [
                {"Name": "nofile", "Soft": 256, "Hard": 256},
                {"Name": "nproc", "Soft": 256, "Hard": 256},
                {"Name": "fsize", "Soft": 67_108_864, "Hard": 67_108_864},
            ],
        },
        "Mounts": [
            {
                "Type": "bind",
                "Source": str(root.resolve()),
                "Destination": "/workspace",
                "RW": True,
                "Propagation": "rprivate",
            }
        ],
        "State": {"Running": True, "Status": "running"},
    }


def test_container_run_profile_has_explicit_resource_and_privilege_limits(
    tmp_path: Path,
) -> None:
    sandbox = ContainerWorkspaceSandbox(
        WorkspaceContext(tmp_path),
        thread_id="security-profile",
    )

    args = sandbox._container_run_args()

    assert args[args.index("--network") + 1] == "none"
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert args[args.index("--security-opt") + 1] == "no-new-privileges=true"
    assert args[args.index("--memory-swap") + 1] == "1g"
    assert args[args.index("--pids-limit") + 1] == "256"
    assert "nofile=256:256" in args
    assert "nproc=256:256" in args
    assert "fsize=67108864:67108864" in args
    assert "--init" in args
    assert "/tmp:rw,noexec,nosuid,nodev,size=64m" in args
    mount = args[args.index("--mount") + 1]
    assert "target=/workspace" in mount
    assert "bind-propagation=rprivate" in mount
    assert "/var/run/docker.sock" not in " ".join(args)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda payload: payload["HostConfig"].update(NetworkMode="bridge"), "network"),
        (lambda payload: payload["HostConfig"].update(ReadonlyRootfs=False), "read_only_rootfs"),
        (lambda payload: payload["HostConfig"].update(CapDrop=[]), "cap_drop"),
        (
            lambda payload: payload["HostConfig"].update(
                SecurityOpt=["no-new-privileges:true", "seccomp=unconfined"]
            ),
            "seccomp_unconfined",
        ),
        (lambda payload: payload["Mounts"].append({"Destination": "/extra"}), "mount_count"),
    ],
)
def test_container_inspect_rejects_security_drift(
    tmp_path: Path,
    mutate: object,
    expected: str,
) -> None:
    sandbox = ContainerWorkspaceSandbox(
        WorkspaceContext(tmp_path),
        thread_id="security-drift",
    )
    payload = copy.deepcopy(_hardened_inspect_payload(sandbox, tmp_path))

    mutate(payload)  # type: ignore[operator]

    assert expected in sandbox._security_violations(payload)


def test_running_drifted_container_is_recreated_only_when_sage_owned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = ContainerWorkspaceSandbox(
        WorkspaceContext(tmp_path),
        thread_id="recreate-drift",
    )
    drifted = _hardened_inspect_payload(sandbox, tmp_path)
    drifted["HostConfig"]["NetworkMode"] = "bridge"  # type: ignore[index]
    hardened = _hardened_inspect_payload(sandbox, tmp_path)
    inspections = iter((drifted, hardened))
    commands: list[list[str]] = []

    monkeypatch.setattr(sandbox, "_require_isolated_daemon", lambda: None)

    def fake_capture(args: list[str], *, check: bool = True) -> str:
        _ = check
        commands.append(args)
        if args[:2] == ["ps", "-q"]:
            return "container-id\n"
        if args[:2] == ["ps", "-aq"]:
            return ""
        if args[0] == "inspect":
            return json.dumps(next(inspections))
        return ""

    monkeypatch.setattr(sandbox, "_docker_capture", fake_capture)

    sandbox._ensure_started()

    assert ["rm", "-f", sandbox._container_name] in commands
    assert any(args[0] == "run" for args in commands)


def test_container_name_collision_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = ContainerWorkspaceSandbox(
        WorkspaceContext(tmp_path),
        thread_id="name-collision",
    )
    payload = _hardened_inspect_payload(sandbox, tmp_path)
    payload["Config"]["Labels"] = {}  # type: ignore[index]
    monkeypatch.setattr(sandbox, "_require_isolated_daemon", lambda: None)
    monkeypatch.setattr(
        sandbox,
        "_docker_capture",
        lambda args, **_kwargs: json.dumps(payload) if args[0] == "inspect" else "id\n",
    )

    with pytest.raises(SandboxPolicyError, match="owned by another workload"):
        sandbox._ensure_started()


def test_discard_owned_removes_only_matching_sage_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式销毁必须核对 Sage 标签和 sandbox_id 后才能删除容器。"""
    expected = ContainerWorkspaceSandbox(
        WorkspaceContext(tmp_path),
        thread_id="discard-owned",
        workspace_id="logical-workspace-id",
    )
    payload = _hardened_inspect_payload(expected, tmp_path)
    commands: list[list[str]] = []

    monkeypatch.setattr(
        ContainerWorkspaceSandbox,
        "_require_isolated_daemon",
        lambda _self: None,
    )

    def fake_capture(
        self: ContainerWorkspaceSandbox,
        args: list[str],
        *,
        check: bool = True,
    ) -> str:
        _ = self, check
        commands.append(args)
        if args[:2] == ["ps", "-aq"]:
            return "container-id\n"
        if args[0] == "inspect":
            return json.dumps(payload)
        return ""

    monkeypatch.setattr(ContainerWorkspaceSandbox, "_docker_capture", fake_capture)

    removed = ContainerWorkspaceSandbox.discard_owned(
        WorkspaceContext(tmp_path),
        thread_id="discard-owned",
        workspace_id="logical-workspace-id",
    )

    assert removed == 1
    assert ["rm", "-f", expected._container_name] in commands


def test_discard_owned_rejects_foreign_container_name_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """确定性容器名被其他 workload 占用时必须 fail closed，不能误删。"""
    expected = ContainerWorkspaceSandbox(
        WorkspaceContext(tmp_path),
        thread_id="discard-foreign",
        workspace_id="logical-workspace-id",
    )
    payload = _hardened_inspect_payload(expected, tmp_path)
    payload["Config"]["Labels"] = {}  # type: ignore[index]
    commands: list[list[str]] = []

    monkeypatch.setattr(
        ContainerWorkspaceSandbox,
        "_require_isolated_daemon",
        lambda _self: None,
    )

    def fake_capture(
        self: ContainerWorkspaceSandbox,
        args: list[str],
        *,
        check: bool = True,
    ) -> str:
        _ = self, check
        commands.append(args)
        if args[:2] == ["ps", "-aq"]:
            return "container-id\n"
        if args[0] == "inspect":
            return json.dumps(payload)
        return ""

    monkeypatch.setattr(ContainerWorkspaceSandbox, "_docker_capture", fake_capture)

    with pytest.raises(SandboxPolicyError, match="owned by another workload"):
        ContainerWorkspaceSandbox.discard_owned(
            WorkspaceContext(tmp_path),
            thread_id="discard-foreign",
            workspace_id="logical-workspace-id",
        )

    assert not any(args[:2] == ["rm", "-f"] for args in commands)


def test_container_sandbox_keeps_logical_identity_while_mounting_execution_root(
    tmp_path: Path,
) -> None:
    """Container descriptor/name 使用逻辑 workspace_id，mount source 仍是 execution root。"""
    execution_root = tmp_path / "execution"
    execution_root.mkdir()
    sandbox = ContainerWorkspaceSandbox(
        WorkspaceContext(execution_root, role="disposable"),
        thread_id="session-identity",
        workspace_id="logical-workspace-id",
    )

    assert sandbox.descriptor.workspace_id == "logical-workspace-id"
    args = sandbox._container_run_args()
    mount = args[args.index("--mount") + 1]
    assert f"source={execution_root.resolve()}" in mount
    assert mount.endswith("target=/workspace,readonly=false,bind-propagation=rprivate")


def test_container_shell_nonzero_exit_is_returned_as_structured_tool_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = ContainerWorkspaceSandbox(
        WorkspaceContext(tmp_path),
        thread_id="shell-error",
    )
    completed = subprocess.CompletedProcess(
        args=["docker", "exec"],
        returncode=13,
        stdout="",
        stderr="permission denied",
    )
    monkeypatch.setattr(sandbox, "_ensure_started", lambda: None)
    monkeypatch.setattr(sandbox, "_docker_run", lambda *_args, **_kwargs: completed)

    result = asyncio.run(sandbox.invoke("run_shell", {"command": "touch /blocked", "timeout": 5}))

    assert result.is_error is True
    assert "exit_code: 13" in result.content
    assert "permission denied" in result.content
