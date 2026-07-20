"""Bounded deployment controller for the Sage private Canary."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, TextIO

_UTC = timezone.utc  # noqa: UP017 - Ubuntu 22.04 system Python is 3.10.

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILE = ROOT / "infra/compose/private-canary.yml"
DEFAULT_ENV_FILE = Path("/etc/sage/env")
DEFAULT_STATE_FILE = Path("/opt/sage/state/deployments.json")
DEFAULT_BACKUP_ROOT = Path("/opt/sage/backups/postgres")
BUILD_TIMEOUT_SECONDS = 60 * 60
COMMIT_TAG = re.compile(r"[0-9a-f]{40}")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_$-]{0,62}")
SECRET_KEYS = {
    "APP_SECRET_KEY",
    "POSTGRES_PASSWORD",
    "REDIS_PASSWORD",
    "GITHUB_OAUTH_CLIENT_SECRET",
    "GITHUB_OAUTH_TRANSACTION_SECRET",
    "GITHUB_TOKEN_ENCRYPTION_SECRET",
    "MODEL_PROVIDER_ENCRYPTION_SECRET",
}
EXPECTED_SERVICES = {"api", "web", "public", "postgres", "redis"}
REQUIRED_KEYS = SECRET_KEYS | {
    "APP_ENV",
    "CLOUD_FRONTEND_URL",
    "GITHUB_OAUTH_CLIENT_ID",
    "GITHUB_OAUTH_REDIRECT_URI",
    "POSTGRES_DB",
    "POSTGRES_USER",
    "SAGE_API_GID",
    "SAGE_API_UID",
    "SAGE_CODING_SANDBOX_IMAGE",
    "SAGE_CODING_SANDBOX_PROVIDER",
    "SAGE_DOCKER_REGISTRY",
    "SAGE_ROOTLESS_DOCKER_SOCKET",
    "SAGE_SANDBOX_DOCKER_SOCKET",
    "SAGE_SANDBOX_UID",
}


class DeployError(RuntimeError):
    """A bounded deployment gate failed."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout: int = 120,
        stdout_file: TextIO | None = None,
    ) -> CommandResult: ...


@dataclass(frozen=True)
class DeployConfig:
    repo_root: Path
    compose_file: Path
    env_file: Path
    state_file: Path
    backup_root: Path
    gateway_url: str


def run_command(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int = 120,
    stdout_file: TextIO | None = None,
) -> CommandResult:
    """Run one argv-only command without a shell."""
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env),
            stdout=stdout_file if stdout_file is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(124, stderr=f"command timed out after {timeout}s")
    except OSError as exc:
        return CommandResult(127, stderr=type(exc).__name__)
    return CommandResult(
        completed.returncode,
        "" if stdout_file is not None else completed.stdout,
        completed.stderr,
    )


def parse_env_file(path: Path) -> dict[str, str]:
    """Read a simple dotenv file without evaluating shell syntax."""
    if not path.is_file():
        raise DeployError(f"环境文件不存在: {path}")
    validate_env_file_metadata(path)
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise DeployError(f"环境文件第 {line_number} 行格式无效")
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise DeployError(f"环境文件第 {line_number} 行变量名无效")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def validate_env_file_metadata(path: Path) -> None:
    """Reject indirection, foreign ownership, and permissive secret files."""
    if path.is_symlink():
        raise DeployError("环境文件不能是符号链接")
    if path.stat().st_uid != os.getuid():
        raise DeployError("环境文件必须属于当前部署用户")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise DeployError(f"环境文件权限必须是 600，当前为 {mode:o}")


def validate_commit_tag(tag: str) -> str:
    """Accept only immutable full Git commit IDs."""
    normalized = tag.strip()
    if COMMIT_TAG.fullmatch(normalized) is None:
        raise DeployError("部署版本必须是 40 位小写 Git commit SHA")
    return normalized


def validate_environment(path: Path, values: Mapping[str, str]) -> None:
    """Reject missing, placeholder, weak, or over-permissive production config."""
    validate_env_file_metadata(path)
    missing = sorted(key for key in REQUIRED_KEYS if not values.get(key, "").strip())
    if missing:
        raise DeployError(f"缺少生产变量: {', '.join(missing)}")
    placeholders = sorted(
        key
        for key, value in values.items()
        if value.strip().upper().startswith("REPLACE_WITH_")
    )
    if placeholders:
        raise DeployError(f"仍有占位变量: {', '.join(placeholders)}")
    weak = sorted(
        key for key in SECRET_KEYS if len(values.get(key, "")) < 32
    )
    if weak:
        raise DeployError(f"生产密钥长度不足: {', '.join(weak)}")
    if values.get("APP_ENV") != "production":
        raise DeployError("APP_ENV 必须是 production")
    if values.get("CLOUD_DEV_LOGIN_ENABLED", "false").lower() != "false":
        raise DeployError("生产环境必须关闭 CLOUD_DEV_LOGIN_ENABLED")
    if values.get("SAGE_CODING_SANDBOX_PROVIDER") != "container":
        raise DeployError("生产 Coding 必须使用 container sandbox")
    for key in ("CLOUD_FRONTEND_URL", "GITHUB_OAUTH_REDIRECT_URI"):
        if not values[key].startswith("https://"):
            raise DeployError(f"{key} 必须使用 HTTPS")
    for key in ("POSTGRES_USER", "POSTGRES_DB"):
        if IDENTIFIER.fullmatch(values[key]) is None:
            raise DeployError(f"{key} 不是安全标识符")
    for key in ("SAGE_API_UID", "SAGE_API_GID", "SAGE_SANDBOX_UID"):
        if not values[key].isdigit() or not 0 <= int(values[key]) <= 65535:
            raise DeployError(f"{key} 不是有效数字 ID")
    registry = values["SAGE_DOCKER_REGISTRY"]
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,252}", registry) is None:
        raise DeployError("SAGE_DOCKER_REGISTRY 不是安全 registry host")
    sandbox_image = values["SAGE_CODING_SANDBOX_IMAGE"]
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,511}", sandbox_image) is None:
        raise DeployError("SAGE_CODING_SANDBOX_IMAGE 不是安全镜像引用")
    expected_sandbox_socket = (
        f"/run/user/{values['SAGE_SANDBOX_UID']}/docker.sock"
    )
    if values["SAGE_SANDBOX_DOCKER_SOCKET"] != expected_sandbox_socket:
        raise DeployError("sandbox daemon 路径与 SAGE_SANDBOX_UID 不一致")


def redact(text: str, values: Mapping[str, str]) -> str:
    """Remove configured secret values from bounded diagnostics."""
    redacted = text
    for key in SECRET_KEYS:
        value = values.get(key, "")
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def deployment_visible_socket_requirements(
    deploy_host: str,
    proxy_socket: str,
) -> tuple[tuple[str, str, int], ...]:
    """Return sockets the unprivileged deployment account must inspect directly."""
    return (
        ("部署", deploy_host.removeprefix("unix://"), os.getuid()),
        ("sandbox 代理", proxy_socket, os.getuid()),
    )


def sandbox_limit_probe_command(docker_host: str, image: str) -> list[str]:
    """Build a no-network probe that exercises every required sandbox limit."""
    return [
        "docker",
        "--host",
        docker_host,
        "run",
        "--rm",
        "--pull=never",
        "--network",
        "none",
        "--read-only",
        "--pids-limit",
        "32",
        "--memory",
        "64m",
        "--cpus",
        "0.25",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        image,
        "python",
        "-c",
        "print('sage-sandbox-limits-ok')",
    ]


class DeployController:
    """Preflight, apply, and roll back one immutable Canary source revision."""

    def __init__(
        self,
        config: DeployConfig,
        *,
        runner: CommandRunner = run_command,
    ) -> None:
        self.config = config
        self.runner = runner
        self.values = parse_env_file(config.env_file)

    def _runtime_env(self, tag: str | None = None) -> dict[str, str]:
        runtime = os.environ.copy()
        runtime.update(self.values)
        runtime["SAGE_ENV_FILE"] = str(self.config.env_file)
        if tag is not None:
            runtime["SAGE_IMAGE_TAG"] = tag
        return runtime

    def _compose(self, *arguments: str) -> list[str]:
        return [
            "docker",
            "compose",
            "--env-file",
            str(self.config.env_file),
            "-f",
            str(self.config.compose_file),
            *arguments,
        ]

    def _run(
        self,
        command: Sequence[str],
        *,
        label: str,
        tag: str | None = None,
        timeout: int = 120,
        stdout_file: TextIO | None = None,
    ) -> CommandResult:
        result = self.runner(
            command,
            cwd=self.config.repo_root,
            env=self._runtime_env(tag),
            timeout=timeout,
            stdout_file=stdout_file,
        )
        if result.returncode != 0:
            detail = redact((result.stderr or result.stdout).strip(), self.values)[-800:]
            raise DeployError(f"{label}失败" + (f": {detail}" if detail else ""))
        return result

    def dry_run_plan(self, action: str, tag: str | None = None) -> dict[str, object]:
        steps = {
            "apply": ["preflight", "build", "database backup", "migration", "health"],
            "rollback": ["preflight", "image check", "switch", "health"],
            "preflight": ["config", "rootless runtimes", "git", "tailscale", "disk"],
        }[action]
        return {"mode": "dry-run", "action": action, "tag": tag, "steps": steps}

    def preflight(self) -> dict[str, object]:
        validate_environment(self.config.env_file, self.values)
        if not self.config.compose_file.is_file():
            raise DeployError(f"Compose 文件不存在: {self.config.compose_file}")
        if shutil.disk_usage(self.config.repo_root).free < 8 * 1024**3:
            raise DeployError("可用磁盘不足 8 GiB")

        deploy_host = os.environ.get("DOCKER_HOST", "")
        proxy_socket = self.values["SAGE_ROOTLESS_DOCKER_SOCKET"]
        sandbox_socket = self.values["SAGE_SANDBOX_DOCKER_SOCKET"]
        if not deploy_host.startswith("unix:///run/user/"):
            raise DeployError("部署 daemon 必须是 /run/user 下的 rootless Docker socket")
        if len({deploy_host.removeprefix("unix://"), proxy_socket, sandbox_socket}) != 3:
            raise DeployError("部署 daemon、sandbox 代理和 sandbox daemon 必须隔离")
        for label, socket_path, expected_uid in deployment_visible_socket_requirements(
            deploy_host,
            proxy_socket,
        ):
            path = Path(socket_path)
            if not path.exists() or not stat.S_ISSOCK(path.stat().st_mode):
                raise DeployError(f"{label} rootless Docker socket 不可用")
            if path.stat().st_uid != expected_uid:
                raise DeployError(f"{label} rootless Docker socket owner 不正确")

        if self.values["SAGE_API_UID"] != "0" or self.values["SAGE_API_GID"] != "0":
            raise DeployError("API 必须使用 rootless user namespace 内的 0:0")

        for label, docker_host in (
            ("部署", deploy_host),
            ("sandbox", f"unix://{proxy_socket}"),
        ):
            result = self._run(
                [
                    "docker",
                    "--host",
                    docker_host,
                    "info",
                    "--format",
                    "{{.CgroupDriver}} {{json .SecurityOptions}}",
                ],
                label=f"检查{label} Docker",
            ).stdout.lower()
            if "rootless" not in result:
                raise DeployError(f"{label} Docker 不是 rootless 模式")
            if label == "sandbox" and not result.startswith("systemd "):
                raise DeployError("sandbox Docker 必须使用 systemd cgroup driver")

        self._run(
            sandbox_limit_probe_command(
                f"unix://{proxy_socket}",
                self.values["SAGE_CODING_SANDBOX_IMAGE"],
            ),
            label="检查 sandbox CPU、内存和 PID 限额",
            timeout=60,
        )

        status_result = self._run(
            ["git", "status", "--porcelain"], label="读取 Git 状态"
        )
        if status_result.stdout.strip():
            raise DeployError("服务器部署工作区不干净")
        head = self._run(["git", "rev-parse", "HEAD"], label="读取 Git commit").stdout.strip()
        self._run(["docker", "compose", "version"], label="检查 Compose")
        self._run(self._compose("config", "--quiet"), label="校验 Compose")
        self._run(["tailscale", "status", "--json"], label="检查 Tailscale")
        return {
            "status": "ready",
            "commit": head,
            "gateway": self.config.gateway_url,
            "secrets": "validated-not-disclosed",
        }

    def status(self) -> dict[str, object]:
        """Return a bounded runtime summary without exposing environment values."""
        state = self._load_state()
        services_result = self._run(
            self._compose("ps", "--format", "json"), label="读取服务状态"
        )
        services: list[dict[str, str]] = []
        raw = services_result.stdout.strip()
        if raw:
            try:
                parsed = json.loads(raw)
                rows = parsed if isinstance(parsed, list) else [parsed]
            except json.JSONDecodeError:
                try:
                    rows = [json.loads(line) for line in raw.splitlines()]
                except json.JSONDecodeError as exc:
                    raise DeployError("服务状态响应格式无效") from exc
            for row in rows:
                if not isinstance(row, dict):
                    continue
                services.append(
                    {
                        "service": str(row.get("Service", row.get("Name", ""))),
                        "state": str(row.get("State", "")),
                        "health": str(row.get("Health", "")),
                    }
                )
        try:
            self._gateway_health()
            gateway = "healthy"
        except DeployError:
            gateway = "unhealthy"
        service_map = {row["service"]: row for row in services}
        services_healthy = EXPECTED_SERVICES.issubset(service_map) and all(
            row["state"] == "running" and row["health"] in {"", "healthy"}
            for name, row in service_map.items()
            if name in EXPECTED_SERVICES
        )
        healthy = gateway == "healthy" and services_healthy
        return {
            "status": "healthy" if healthy else "degraded",
            "commit": self._run(
                ["git", "rev-parse", "HEAD"], label="读取 Git commit"
            ).stdout.strip(),
            "current": state.get("current"),
            "previous": state.get("previous"),
            "deployed_at": state.get("deployed_at", state.get("rolled_back_at")),
            "gateway": gateway,
            "services": services,
        }

    def apply(self, tag: str, *, execute: bool) -> dict[str, object]:
        tag = validate_commit_tag(tag)
        if not execute:
            return self.dry_run_plan("apply", tag)
        report = self.preflight()
        if report["commit"] != tag:
            raise DeployError("指定部署版本与服务器当前 commit 不一致")

        state = self._load_state()
        previous = str(state.get("current", "")) or None
        self._run(
            self._compose("build", "api", "web", "public"),
            label="构建镜像",
            tag=tag,
            timeout=BUILD_TIMEOUT_SECONDS,
        )
        self._run(
            self._compose("up", "-d", "--wait", "postgres", "redis"),
            label="启动数据服务",
            tag=tag,
            timeout=300,
        )
        backup = self._backup_database(tag)
        self._run(
            self._compose("run", "--rm", "--no-deps", "api", "python", "-m", "db.migrations"),
            label="执行数据库迁移",
            tag=tag,
            timeout=600,
        )
        try:
            self._run(
                self._compose("up", "-d", "--wait", "api", "web", "public"),
                label="切换应用",
                tag=tag,
                timeout=600,
            )
            self._gateway_health()
        except Exception:
            if previous and COMMIT_TAG.fullmatch(previous):
                self._run(
                    self._compose(
                        "up", "-d", "--no-build", "--wait", "api", "web", "public"
                    ),
                    label="恢复上一镜像",
                    tag=previous,
                    timeout=600,
                )
            raise

        deployed_at = datetime.now(_UTC).isoformat()
        self._write_state(
            {
                "current": tag,
                "previous": previous,
                "backup": str(backup),
                "deployed_at": deployed_at,
            }
        )
        return {
            "status": "deployed",
            "tag": tag,
            "previous": previous,
            "backup": str(backup),
            "deployed_at": deployed_at,
        }

    def rollback(self, tag: str, *, execute: bool) -> dict[str, object]:
        tag = validate_commit_tag(tag)
        if not execute:
            return self.dry_run_plan("rollback", tag)
        self.preflight()
        for image in (f"sage-api:{tag}", f"sage-web:{tag}", f"sage-public:{tag}"):
            self._run(["docker", "image", "inspect", image], label="检查回滚镜像")
        state = self._load_state()
        current = str(state.get("current", "")) or None
        self._run(
            self._compose(
                "up", "-d", "--no-build", "--wait", "api", "web", "public"
            ),
            label="切换回滚镜像",
            tag=tag,
            timeout=600,
        )
        self._gateway_health()
        rolled_back_at = datetime.now(_UTC).isoformat()
        self._write_state(
            {
                "current": tag,
                "previous": current,
                "backup": state.get("backup"),
                "rolled_back_at": rolled_back_at,
            }
        )
        return {
            "status": "rolled-back",
            "tag": tag,
            "previous": current,
            "database": "forward-schema-preserved",
        }

    def _backup_database(self, tag: str) -> Path:
        self.config.backup_root.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(_UTC).strftime("%Y%m%dT%H%M%SZ")
        target = self.config.backup_root / f"{timestamp}-{tag}.sql"
        target.touch(mode=0o600, exist_ok=False)
        with target.open("w", encoding="utf-8") as output:
            self._run(
                self._compose(
                    "exec",
                    "-T",
                    "postgres",
                    "pg_dump",
                    "--no-owner",
                    "--no-privileges",
                    "-U",
                    self.values["POSTGRES_USER"],
                    "-d",
                    self.values["POSTGRES_DB"],
                ),
                label="备份 PostgreSQL",
                tag=tag,
                timeout=600,
                stdout_file=output,
            )
        return target

    def _gateway_health(self) -> None:
        try:
            with urllib.request.urlopen(self.config.gateway_url, timeout=10) as response:
                if response.status != 200:
                    raise DeployError(f"网关健康检查返回 {response.status}")
        except OSError as exc:
            raise DeployError("网关健康检查失败") from exc

    def _load_state(self) -> dict[str, object]:
        if not self.config.state_file.is_file():
            return {}
        try:
            value = json.loads(self.config.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DeployError("部署状态文件损坏") from exc
        if not isinstance(value, dict):
            raise DeployError("部署状态文件格式无效")
        return value

    def _write_state(self, value: Mapping[str, object]) -> None:
        self.config.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config.state_file.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(self.config.state_file)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sage 私有 Canary 受限部署控制器")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--backup-root", type=Path, default=DEFAULT_BACKUP_ROOT)
    parser.add_argument("--gateway-url", default="http://127.0.0.1:8080/health")
    parser.add_argument("--execute", action="store_true", help="显式允许 apply/rollback 写操作")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    subparsers.add_parser("status")
    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--tag", required=True)
    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--tag", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = DeployConfig(
        repo_root=args.repo_root.resolve(),
        compose_file=args.compose_file.resolve(),
        env_file=args.env_file.expanduser().absolute(),
        state_file=args.state_file.resolve(),
        backup_root=args.backup_root.resolve(),
        gateway_url=args.gateway_url,
    )
    try:
        controller = DeployController(config)
        if args.command == "preflight":
            result = controller.preflight()
        elif args.command == "status":
            result = controller.status()
        elif args.command == "apply":
            result = controller.apply(args.tag, execute=args.execute)
        else:
            result = controller.rollback(args.tag, execute=args.execute)
    except DeployError as exc:
        print(f"deployctl blocked: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
