#!/usr/bin/env python3
"""受限的本地 private Canary 与 public facade CI/CD 控制器。

它只把已经合入 ``dev/sage-v7`` 且 GitHub 必需检查全绿的完整 commit SHA
部署到私有 Canary，并把同一 SHA 的隔离 public 镜像发布到公网门面。服务器上的实际
构建、备份、迁移和健康检查仍由 ``scripts/deployctl.py`` 与 root-owned
``public_releasectl`` 执行；本机控制器不读取服务器密钥或环境文件。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_COMMIT = re.compile(r"[0-9a-f]{40}")
_REMOTE_HOST = re.compile(r"[A-Za-z0-9._-]+@[A-Za-z0-9._-]+")
_DOCKER_HOST = re.compile(r"unix:///run/user/[0-9]+/docker\.sock")
_BRANCH = "dev/sage-v7"
_REQUIRED_CHECKS = ("python", "backend-quality", "frontend-quality", "public-release")
LABEL = "com.sage.canaryctl"
DEFAULT_STATE_ROOT = Path.home() / ".local/state/sage-canary"
DEFAULT_LAUNCHER = Path.home() / ".local/bin/sage-canaryctl"
DEFAULT_PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
DEFAULT_REMOTE_HOST = "sage-deploy@121.40.185.188"
DEFAULT_REMOTE_APP = "/opt/sage/app"
DEFAULT_ENV_FILE = "/etc/sage/env"
DEFAULT_DOCKER_HOST = "unix:///run/user/1002/docker.sock"
DEFAULT_HEALTH_URL = "https://sage-agent-canary.tail74531c.ts.net/health"
DEFAULT_PUBLIC_HEALTH_URL = "https://sagecompanion.top/"
DEFAULT_GITHUB_REPO = "ZeroMadLife/sage-agent"
DEFAULT_HOST_KEY_ALIAS = "121.40.185.188"
DEFAULT_GIT_BIN = shutil.which("git") or "/usr/bin/git"
DEFAULT_GH_BIN = shutil.which("gh") or "gh"
DEFAULT_SSH_BIN = shutil.which("ssh") or "/usr/bin/ssh"
DEFAULT_CURL_BIN = shutil.which("curl") or "/usr/bin/curl"
DEFAULT_OPENSSL_BIN = shutil.which("openssl") or "/usr/bin/openssl"


class CanaryError(RuntimeError):
    """A bounded CI/CD or availability gate failed."""


class CanaryDeferred(CanaryError):
    """A normal external gate is incomplete and should be checked later."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class CanaryConfig:
    repo_root: Path
    remote_host: str
    remote_app: str
    ssh_key: Path
    env_file: str
    docker_host: str
    health_url: str
    public_health_url: str
    github_repo: str
    branch: str
    state_root: Path
    host_key_alias: str = DEFAULT_HOST_KEY_ALIAS
    git_bin: str = DEFAULT_GIT_BIN
    gh_bin: str = DEFAULT_GH_BIN
    ssh_bin: str = DEFAULT_SSH_BIN
    curl_bin: str = DEFAULT_CURL_BIN
    openssl_bin: str = DEFAULT_OPENSSL_BIN
    cc_connect_bin: str = "cc-connect"
    notify_project: str | None = None
    notify_session: str | None = None
    auto_deploy: bool = True

    @property
    def state_path(self) -> Path:
        return self.state_root / "status.json"

    @property
    def config_path(self) -> Path:
        return self.state_root / "config.json"


Runner = Callable[[Sequence[str], str | None, int], CommandResult]
HttpProbe = Callable[[str], bool]


def run_command(
    command: Sequence[str],
    input_text: str | None = None,
    timeout: int = 30,
) -> CommandResult:
    """Run an argv-only command without a shell."""
    try:
        completed = subprocess.run(
            list(command),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(returncode=124, stderr=type(exc).__name__)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def _write_private(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        os.chmod(path, mode)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(path: Path, value: Mapping[str, object]) -> None:
    _write_private(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )


def _load_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CanaryError("Canary 状态文件损坏") from exc
    if not isinstance(value, dict):
        raise CanaryError("Canary 状态文件格式无效")
    return value


def _lock_or_skip(path: Path) -> int | None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    return descriptor


def validate_commit(value: str) -> str:
    value = value.strip()
    if _COMMIT.fullmatch(value) is None:
        raise CanaryError("部署版本必须是 40 位小写 Git commit SHA")
    return value


def validate_config(config: CanaryConfig) -> None:
    if not _REMOTE_HOST.fullmatch(config.remote_host):
        raise CanaryError("远程主机必须是 user@host 格式")
    if not re.fullmatch(r"[A-Za-z0-9._:-]+", config.host_key_alias):
        raise CanaryError("SSH host key alias 无效")
    if not config.remote_app.startswith("/") or "\n" in config.remote_app:
        raise CanaryError("远程应用目录必须是绝对路径")
    if not Path(config.ssh_key).is_file():
        raise CanaryError("SSH 部署密钥不存在")
    if _DOCKER_HOST.fullmatch(config.docker_host) is None:
        raise CanaryError("远程 Docker 必须使用 /run/user 下的 rootless socket")
    if not config.health_url.startswith(("https://", "http://127.0.0.1")):
        raise CanaryError("健康检查地址必须使用 HTTPS 或本机回环地址")
    if not config.public_health_url.startswith(("https://", "http://")):
        raise CanaryError("公共门面健康检查地址必须使用 HTTP(S)")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", config.github_repo):
        raise CanaryError("GitHub 仓库标识无效")
    if config.branch != _BRANCH:
        raise CanaryError("Canary 只允许部署 dev/sage-v7")
    for name, executable in (
        ("git", config.git_bin),
        ("gh", config.gh_bin),
        ("ssh", config.ssh_bin),
        ("curl", config.curl_bin),
        ("openssl", config.openssl_bin),
        ("cc-connect", config.cc_connect_bin),
    ):
        path = Path(executable).expanduser()
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise CanaryError(f"{name} binary 不可用")


def config_from_values(values: Mapping[str, object]) -> CanaryConfig:
    def string(name: str, default: str = "") -> str:
        value = values.get(name, default)
        return str(value)

    config = CanaryConfig(
        repo_root=Path(string("repo_root", str(ROOT))).expanduser().resolve(),
        remote_host=string("remote_host", DEFAULT_REMOTE_HOST),
        remote_app=string("remote_app", DEFAULT_REMOTE_APP),
        ssh_key=Path(string("ssh_key", str(Path.home() / ".ssh/id_ed25519_sage_deploy")))
        .expanduser()
        .resolve(),
        env_file=string("env_file", DEFAULT_ENV_FILE),
        docker_host=string("docker_host", DEFAULT_DOCKER_HOST),
        health_url=string("health_url", DEFAULT_HEALTH_URL),
        public_health_url=string("public_health_url", DEFAULT_PUBLIC_HEALTH_URL),
        github_repo=string("github_repo", DEFAULT_GITHUB_REPO),
        branch=string("branch", _BRANCH),
        state_root=Path(string("state_root", str(DEFAULT_STATE_ROOT))).expanduser().resolve(),
        host_key_alias=string("host_key_alias", DEFAULT_HOST_KEY_ALIAS),
        git_bin=string("git_bin", DEFAULT_GIT_BIN),
        gh_bin=string("gh_bin", DEFAULT_GH_BIN),
        ssh_bin=string("ssh_bin", DEFAULT_SSH_BIN),
        curl_bin=string("curl_bin", DEFAULT_CURL_BIN),
        openssl_bin=string("openssl_bin", DEFAULT_OPENSSL_BIN),
        cc_connect_bin=string("cc_connect_bin", "cc-connect"),
        notify_project=string("notify_project") or None,
        notify_session=string("notify_session") or None,
        auto_deploy=(
            str(values.get("auto_deploy", True)).lower() == "true"
            if isinstance(values.get("auto_deploy", True), str)
            else bool(values.get("auto_deploy", True))
        ),
    )
    validate_config(config)
    return config


def _default_state() -> dict[str, object]:
    return {
        "state": "UNKNOWN",
        "checked_at": None,
        "last_healthy_at": None,
        "last_error": None,
        "consecutive_failures": 0,
        "last_deployed_sha": None,
        "last_public_deployed_sha": None,
        "last_sync_at": None,
        "last_sync_error": None,
        "consecutive_sync_failures": 0,
        "auto_deploy_paused": False,
        "last_notification": None,
    }


def load_state(path: Path) -> dict[str, object]:
    state = _default_state()
    state.update(_load_json(path))
    return state


def _int_state(state: Mapping[str, object], key: str) -> int:
    value = state.get(key, 0)
    try:
        if isinstance(value, str | bytes | bytearray | int | float):
            return int(value)
        return 0
    except (TypeError, ValueError, OverflowError):
        return 0


def probe_http(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return bool(response.status == 200)
    except (OSError, ValueError):
        return False


def probe_public_http(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            body = response.read(4096)
            return response.status == 200 and b"<title>ZeroMadLife / Sage</title>" in body
    except (OSError, ValueError):
        return False


def parse_check_runs(payload: str) -> dict[str, str]:
    """Return the latest conclusion for each required GitHub check."""
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CanaryError("GitHub checks 响应不是有效 JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("check_runs"), list):
        raise CanaryError("GitHub checks 响应格式无效")
    latest: dict[str, tuple[str, str, str]] = {}
    for raw in value["check_runs"]:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", ""))
        if name not in _REQUIRED_CHECKS:
            continue
        stamp = str(raw.get("completed_at") or raw.get("started_at") or "")
        status = str(raw.get("status", ""))
        conclusion = str(raw.get("conclusion") or "")
        previous = latest.get(name)
        if previous is None or stamp >= previous[0]:
            latest[name] = (stamp, status, conclusion)
    missing = [name for name in _REQUIRED_CHECKS if name not in latest]
    if missing:
        raise CanaryDeferred(f"GitHub CI 尚未提供必需检查: {', '.join(missing)}")
    incomplete = [
        name
        for name, (_, status, _) in latest.items()
        if status != "completed"
    ]
    if incomplete:
        raise CanaryDeferred(f"GitHub CI 仍在运行: {', '.join(sorted(incomplete))}")
    failed = [
        name
        for name, (_, _, conclusion) in latest.items()
        if conclusion != "success"
    ]
    if failed:
        raise CanaryError(f"GitHub CI 未全绿: {', '.join(sorted(failed))}")
    return {name: latest[name][2] for name in _REQUIRED_CHECKS}


class CanaryController:
    def __init__(
        self,
        config: CanaryConfig,
        runner: Runner = run_command,
        http_probe: HttpProbe = probe_http,
        public_http_probe: HttpProbe = probe_public_http,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.runner = runner
        self.http_probe = http_probe
        self.public_http_probe = public_http_probe
        self.clock = clock

    def _local(self, command: Sequence[str], timeout: int = 30) -> CommandResult:
        return self.runner(command, None, timeout)

    def _ssh(self, command: Sequence[str], timeout: int = 60) -> CommandResult:
        return self._ssh_script(shlex.join(list(command)), timeout=timeout)

    def _ssh_script(self, script: str, timeout: int = 60) -> CommandResult:
        script = "set -eu\n" + script
        return self.runner(
            [
                self.config.ssh_bin,
                "-i",
                str(self.config.ssh_key),
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "ServerAliveInterval=30",
                "-o",
                "ServerAliveCountMax=20",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                f"HostKeyAlias={self.config.host_key_alias}",
                self.config.remote_host,
                "sh",
                "-s",
                "--",
            ],
            script,
            timeout,
        )

    @staticmethod
    def _command_failed(result: CommandResult) -> bool:
        return result.returncode != 0

    def latest_sha(self) -> str:
        result = self._local(
            [self.config.git_bin, "-C", str(self.config.repo_root), "ls-remote", "origin", f"refs/heads/{self.config.branch}"]
        )
        if self._command_failed(result):
            raise CanaryError("无法读取 origin/dev/sage-v7")
        parts = result.stdout.split()
        if not parts:
            raise CanaryError("origin/dev/sage-v7 不存在")
        return validate_commit(parts[0])

    def ci_checks(self, sha: str) -> dict[str, str]:
        sha = validate_commit(sha)
        result = self._local(
            [
                self.config.gh_bin,
                "api",
                f"repos/{self.config.github_repo}/commits/{sha}/check-runs?per_page=100",
            ],
            timeout=60,
        )
        if self._command_failed(result):
            raise CanaryError("GitHub CLI 认证或检查状态不可用")
        return parse_check_runs(result.stdout)

    def remote_head(self) -> str:
        result = self._ssh(
            ["git", "-C", self.config.remote_app, "rev-parse", "HEAD"], timeout=30
        )
        if self._command_failed(result):
            raise CanaryError("无法读取 Canary 当前 commit")
        return validate_commit(result.stdout.strip())

    def remote_status(self) -> dict[str, object]:
        result = self._ssh_script(
            "DOCKER_HOST="
            + shlex.quote(self.config.docker_host)
            + " "
            + shlex.join(
                [
                    "python3",
                    f"{self.config.remote_app}/scripts/deployctl.py",
                    "--env-file",
                    self.config.env_file,
                    "status",
                ]
            ),
            timeout=60,
        )
        if self._command_failed(result):
            raise CanaryError("无法读取 Canary 服务状态")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CanaryError("Canary 服务状态响应格式无效") from exc
        if not isinstance(value, dict):
            raise CanaryError("Canary 服务状态响应格式无效")
        return value

    def _remote_public_request(
        self, action: str, tag: str | None = None, *, timeout: int = 900
    ) -> dict[str, object]:
        if action not in {"status", "apply", "rollback"}:
            raise CanaryError("公共门面发布 action 无效")
        request: dict[str, str] = {"action": action}
        if action != "status":
            if tag is None:
                raise CanaryError("公共门面发布缺少版本")
            request["tag"] = validate_commit(tag)
        payload = json.dumps(request, separators=(",", ":"), sort_keys=True)
        command = (
            "printf '%s\\n' "
            + shlex.quote(payload)
            + " | sudo -n /usr/local/sbin/sage-public-releasectl"
        )
        result = self._ssh_script(command, timeout=timeout)
        if self._command_failed(result):
            raise CanaryError("公共门面受限发布失败")
        try:
            value = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CanaryError("公共门面发布响应格式无效") from exc
        if not isinstance(value, dict):
            raise CanaryError("公共门面发布响应格式无效")
        return value

    def remote_public_status(self) -> dict[str, object]:
        return self._remote_public_request("status", timeout=60)

    def availability(self) -> dict[str, object]:
        http_ok = self._http_healthy()
        public_http_ok = self._public_http_healthy()
        remote_ok = True
        public_remote_ok = True
        status: dict[str, object] | None = None
        public_status: dict[str, object] | None = None
        if http_ok:
            try:
                status = self.remote_status()
                remote_ok = status.get("status") == "healthy"
            except CanaryError:
                remote_ok = False
        else:
            remote_ok = False
        if public_http_ok:
            try:
                public_status = self.remote_public_status()
                public_remote_ok = public_status.get("status") == "healthy"
            except CanaryError:
                public_remote_ok = False
        else:
            public_remote_ok = False
        return {
            "healthy": http_ok and remote_ok and public_http_ok and public_remote_ok,
            "private_healthy": http_ok and remote_ok,
            "public_healthy": public_http_ok and public_remote_ok,
            "http": http_ok,
            "remote": remote_ok,
            "status": status,
            "public_http": public_http_ok,
            "public_remote": public_remote_ok,
            "public_status": public_status,
        }

    def _http_healthy(self) -> bool:
        if self.http_probe(self.config.health_url):
            return True
        curl = self._local(
            [
                self.config.curl_bin,
                "--noproxy",
                "*",
                "--fail",
                "--silent",
                "--show-error",
                "--output",
                "/dev/null",
                "--max-time",
                "15",
                self.config.health_url,
            ],
            timeout=20,
        )
        if curl.returncode == 0:
            return True
        return self._remote_loopback_http_healthy()

    def _remote_loopback_http_healthy(self) -> bool:
        result = self._ssh(
            [
                "curl",
                "--noproxy",
                "*",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                "15",
                "http://127.0.0.1:8080/health",
            ],
            timeout=20,
        )
        if result.returncode != 0:
            return False
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        return isinstance(payload, dict) and payload.get("status") == "ok"

    def _public_http_healthy(self) -> bool:
        if self.public_http_probe(self.config.public_health_url):
            return True
        curl = self._local(
            [
                self.config.curl_bin,
                "--noproxy",
                "*",
                "--fail",
                "--silent",
                "--show-error",
                "--max-time",
                "15",
                self.config.public_health_url,
            ],
            timeout=20,
        )
        if (
            curl.returncode == 0
            and "<title>ZeroMadLife / Sage</title>" in curl.stdout
        ):
            return True
        return self._openssl_public_http_healthy()

    def _openssl_public_http_healthy(self) -> bool:
        try:
            parsed = urllib.parse.urlsplit(self.config.public_health_url)
            hostname = parsed.hostname
            port = parsed.port or 443
        except ValueError:
            return False
        if parsed.scheme != "https" or not hostname:
            return False
        target = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
        if any(character in target for character in "\r\n"):
            return False
        host_header = hostname if port == 443 else f"{hostname}:{port}"
        request = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Connection: close\r\n\r\n"
        )
        result = self.runner(
            [
                self.config.openssl_bin,
                "s_client",
                "-quiet",
                "-connect",
                f"{hostname}:{port}",
                "-servername",
                hostname,
            ],
            request,
            20,
        )
        return (
            result.returncode == 0
            and result.stdout.startswith("HTTP/1.1 200 ")
            and "<title>ZeroMadLife / Sage</title>" in result.stdout
        )

    def _wait_public_http_healthy(self, attempts: int = 30) -> bool:
        for attempt in range(attempts):
            if self._public_http_healthy():
                return True
            if attempt + 1 < attempts:
                time.sleep(1)
        return False

    def _notify(self, message: str) -> bool:
        if not self.config.notify_project or not self.config.notify_session:
            return False
        result = self.runner(
            [
                self.config.cc_connect_bin,
                "send",
                "--stdin",
                "--project",
                self.config.notify_project,
                "--session",
                self.config.notify_session,
            ],
            message,
            20,
        )
        return result.returncode == 0

    def check(self, *, notify: bool = True) -> dict[str, object]:
        report = self.availability()
        state = load_state(self.config.state_path)
        old_state = str(state.get("state", "UNKNOWN"))
        now = self.clock()
        if bool(report["healthy"]):
            recovered = old_state == "UNHEALTHY"
            state.update(
                {
                    "state": "HEALTHY",
                    "checked_at": now,
                    "last_healthy_at": now,
                    "last_error": None,
                    "consecutive_failures": 0,
                }
            )
            if recovered and notify:
                sent = self._notify("[Sage Canary] 服务已恢复。健康检查和服务器状态均已通过。")
                if sent:
                    state["last_notification"] = "recovered"
        else:
            failure_count = _int_state(state, "consecutive_failures") + 1
            state.update(
                {
                    "state": "UNHEALTHY",
                    "checked_at": now,
                    "last_error": "HTTP 健康检查或服务器服务状态失败",
                    "consecutive_failures": failure_count,
                }
            )
            if old_state != "UNHEALTHY" and notify:
                sent = self._notify(
                    "[Sage Canary] 可用性异常。HTTP 健康检查或服务器服务状态失败；未自动重启或回滚，请人工查看。"
                )
                if sent:
                    state["last_notification"] = "unhealthy"
        _write_json(self.config.state_path, state)
        report["state"] = state["state"]
        return report

    def _remote_deploy_script(self, sha: str) -> str:
        sha = validate_commit(sha)
        deployctl = f"{self.config.remote_app}/scripts/deployctl.py"
        q = shlex.quote
        return "\n".join(
            (
                'test -z "$(git -C ' + q(self.config.remote_app) + ' status --porcelain)"',
                "fetched=0",
                "attempt=1",
                "while [ \"$attempt\" -le 3 ]; do",
                "  if "
                + " ".join(
                    (
                        "git",
                        "-C",
                        q(self.config.remote_app),
                        "fetch",
                        "--prune",
                        "origin",
                        q(f"refs/heads/{self.config.branch}"),
                    )
                )
                + "; then fetched=1; break; fi",
                "  attempt=$((attempt + 1))",
                "  if [ \"$attempt\" -le 3 ]; then sleep 10; fi",
                "done",
                "test \"$fetched\" = 1",
                " ".join(
                    (
                        "git",
                        "-C",
                        q(self.config.remote_app),
                        "checkout",
                        "--detach",
                        q(sha),
                    )
                ),
                "test \"$(git -C "
                + q(self.config.remote_app)
                + " rev-parse HEAD)\" = "
                + q(sha),
                " ".join(
                    (
                        "DOCKER_HOST=" + q(self.config.docker_host),
                        "python3",
                        q(deployctl),
                        "--env-file",
                        q(self.config.env_file),
                        "--execute",
                        "cleanup",
                    )
                ),
                " ".join(
                    (
                        "DOCKER_HOST=" + q(self.config.docker_host),
                        "python3",
                        q(deployctl),
                        "--env-file",
                        q(self.config.env_file),
                        "preflight",
                    )
                ),
                " ".join(
                    (
                        "DOCKER_HOST=" + q(self.config.docker_host),
                        "python3",
                        q(deployctl),
                        "--env-file",
                        q(self.config.env_file),
                        "--execute",
                        "apply",
                        "--tag",
                        q(sha),
                    )
                ),
            )
        )

    def deploy(self, sha: str) -> dict[str, object]:
        sha = validate_commit(sha)
        self.ci_checks(sha)
        current_head = self.remote_head()
        try:
            remote_status = self.remote_status()
        except CanaryError:
            remote_status = {}
        try:
            public_status = self.remote_public_status()
        except CanaryError:
            public_status = {}
        current_deployed = str(remote_status.get("current") or "")
        current_public = str(public_status.get("current") or "")
        private_ready = current_deployed == sha and remote_status.get("status") == "healthy"
        public_ready = current_public == sha and public_status.get("status") == "healthy"
        if private_ready and public_ready:
            state = load_state(self.config.state_path)
            state.update(
                {
                    "last_deployed_sha": sha,
                    "last_public_deployed_sha": sha,
                    "last_sync_at": self.clock(),
                    "last_sync_error": None,
                    "consecutive_sync_failures": 0,
                    "auto_deploy_paused": False,
                }
            )
            _write_json(self.config.state_path, state)
            return {"status": "up-to-date", "sha": sha}
        if not private_ready:
            result = self._ssh_script(self._remote_deploy_script(sha), timeout=7200)
            if self._command_failed(result):
                raise CanaryError("Canary 部署失败；旧服务应保持不变，请查看服务器 deployctl 状态")
        public_result = self._remote_public_request("apply", sha)
        if not self._wait_public_http_healthy():
            previous_public = str(public_result.get("previous") or "")
            if _COMMIT.fullmatch(previous_public):
                self._remote_public_request("rollback", previous_public)
            raise CanaryError("公共门面外部健康检查失败，已尝试恢复上一版本")
        state = load_state(self.config.state_path)
        state.update(
            {
                "last_deployed_sha": sha,
                "last_public_deployed_sha": sha,
                "last_sync_at": self.clock(),
                "last_sync_error": None,
                "consecutive_sync_failures": 0,
                "auto_deploy_paused": False,
            }
        )
        _write_json(self.config.state_path, state)
        return {
            "status": "deployed",
            "sha": sha,
            "previous": current_deployed or current_head,
            "public_previous": public_result.get("previous"),
        }

    def sync(self) -> dict[str, object]:
        if not self.config.auto_deploy:
            return {"status": "disabled"}
        state = load_state(self.config.state_path)
        if bool(state.get("auto_deploy_paused")):
            return {"status": "paused"}
        try:
            result = self.deploy(self.latest_sha())
        except CanaryDeferred as exc:
            state.update(
                {
                    "last_sync_error": str(exc),
                    "auto_deploy_paused": False,
                }
            )
            _write_json(self.config.state_path, state)
            return {"status": "waiting-ci", "reason": str(exc)}
        except CanaryError:
            count = _int_state(state, "consecutive_sync_failures") + 1
            paused = count >= 3
            state.update(
                {
                    "last_sync_error": "CI、SSH 或 deployctl 门禁失败",
                    "consecutive_sync_failures": count,
                    "auto_deploy_paused": paused,
                }
            )
            _write_json(self.config.state_path, state)
            if (
                paused
                and state.get("last_notification") != "deployment-paused"
                and self._notify(
                    "[Sage Canary] 自动部署已连续失败 3 次，已暂停；请人工检查 CI、SSH 或 deployctl。"
                )
            ):
                state["last_notification"] = "deployment-paused"
                _write_json(self.config.state_path, state)
            raise
        return result

    def run_once(self) -> dict[str, object]:
        report = self.check()
        if not bool(report["private_healthy"]):
            return {"status": "unhealthy", "availability": report}
        result = self.sync()
        if result.get("status") in {"deployed", "up-to-date"}:
            if not self._http_healthy():
                raise CanaryError("部署后私有 Canary 健康检查失败")
            if not self._public_http_healthy():
                raise CanaryError("部署后公共门面健康检查失败")
            state = load_state(self.config.state_path)
            now = self.clock()
            state.update(
                {
                    "state": "HEALTHY",
                    "checked_at": now,
                    "last_healthy_at": now,
                    "last_error": None,
                    "consecutive_failures": 0,
                }
            )
            _write_json(self.config.state_path, state)
        elif not bool(report["public_healthy"]):
            return {"status": "unhealthy", "availability": report, "sync": result}
        return {"status": "ok", "availability": report, "sync": result}


def _cron_binding(cc_connect_bin: str, cron_id: str) -> tuple[str, str]:
    result = run_command([cc_connect_bin, "cron", "info", cron_id], timeout=15)
    if result.returncode != 0:
        raise CanaryError("无法读取通知 cron 绑定")
    try:
        value = json.loads(result.stdout)
        project = str(value["project"])
        session = str(value["session_key"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise CanaryError("通知 cron 绑定格式无效") from exc
    if not project or not session:
        raise CanaryError("通知 cron 绑定为空")
    return project, session


def _launcher_text(python_bin: str, script: Path, config: Path, launcher: Path, plist: Path) -> str:
    base = " ".join(shlex.quote(part) for part in (python_bin, str(script)))
    uninstall = " ".join(
        shlex.quote(part)
        for part in (
            python_bin,
            str(script),
            "uninstall",
            "--state-root",
            str(config.parent),
            "--launcher",
            str(launcher),
            "--plist",
            str(plist),
        )
    )
    quoted = shlex.quote(str(config))
    return (
        "#!/bin/sh\nset -eu\n"
        "command_name=${1:-run}\n"
        "case \"$command_name\" in\n"
        "  run|check|status|doctor|resume)\n"
        "    if [ \"$#\" -gt 0 ]; then shift; fi\n"
        f"    exec {base} --config {quoted} \"$command_name\" \"$@\"\n"
        "    ;;\n"
        "  uninstall)\n"
        "    shift\n"
        f"    exec {uninstall} \"$@\"\n"
        "    ;;\n"
        "  *) echo 'usage: sage-canaryctl [run|check|status|doctor|resume|uninstall]' >&2; exit 2;;\n"
        "esac\n"
    )


def _plist_bytes(launcher: Path, interval_seconds: int) -> bytes:
    payload = {
        "Label": LABEL,
        "ProgramArguments": [str(launcher)],
        "RunAtLoad": True,
        "StartInterval": interval_seconds,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "EnvironmentVariables": {"HOME": str(Path.home())},
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)


def install_command(args: argparse.Namespace) -> int:
    source_bin = Path(args.cc_connect_bin).expanduser()
    resolved = shutil.which(str(source_bin)) if not source_bin.is_absolute() else str(source_bin)
    if not resolved or not Path(resolved).is_file() or not os.access(resolved, os.X_OK):
        raise CanaryError("cc-connect binary 不可用")
    project, session = _cron_binding(resolved, args.source_cron_id)
    state_root = Path(args.state_root).expanduser().resolve()
    config = config_from_values(
        {
            "repo_root": args.repo_root,
            "remote_host": args.remote_host,
            "remote_app": args.remote_app,
            "ssh_key": args.ssh_key,
            "env_file": args.env_file,
            "docker_host": args.docker_host,
            "health_url": args.health_url,
            "public_health_url": args.public_health_url,
            "github_repo": args.github_repo,
            "branch": _BRANCH,
            "state_root": state_root,
            "host_key_alias": args.host_key_alias,
            "git_bin": DEFAULT_GIT_BIN,
            "gh_bin": DEFAULT_GH_BIN,
            "ssh_bin": DEFAULT_SSH_BIN,
            "curl_bin": DEFAULT_CURL_BIN,
            "cc_connect_bin": resolved,
            "notify_project": project,
            "notify_session": session,
            "auto_deploy": not args.disable_auto_deploy,
        }
    )
    _write_json(
        config.config_path,
        {
            "repo_root": str(config.repo_root),
            "remote_host": config.remote_host,
            "remote_app": config.remote_app,
            "ssh_key": str(config.ssh_key),
            "env_file": config.env_file,
            "docker_host": config.docker_host,
            "health_url": config.health_url,
            "public_health_url": config.public_health_url,
            "github_repo": config.github_repo,
            "branch": config.branch,
            "state_root": str(config.state_root),
            "host_key_alias": config.host_key_alias,
            "git_bin": config.git_bin,
            "gh_bin": config.gh_bin,
            "ssh_bin": config.ssh_bin,
            "curl_bin": config.curl_bin,
            "openssl_bin": config.openssl_bin,
            "cc_connect_bin": config.cc_connect_bin,
            "notify_project": config.notify_project,
            "notify_session": config.notify_session,
            "auto_deploy": config.auto_deploy,
        },
    )
    launcher = Path(args.launcher).expanduser().resolve()
    plist = Path(args.plist).expanduser().resolve()
    _write_private(
        launcher,
        _launcher_text(sys.executable, Path(__file__).resolve(), config.config_path, launcher, plist).encode("utf-8"),
        0o700,
    )
    _write_private(plist, _plist_bytes(launcher, args.interval_seconds), 0o600)
    if not args.no_load:
        domain = f"gui/{os.getuid()}"
        run_command(["/bin/launchctl", "bootout", domain, str(plist)], timeout=10)
        loaded = run_command(["/bin/launchctl", "bootstrap", domain, str(plist)], timeout=10)
        if loaded.returncode != 0:
            raise CanaryError("Canary LaunchAgent 加载失败")
    print(f"installed: {LABEL}")
    print(f"status: {launcher} status")
    return 0


def uninstall_command(args: argparse.Namespace) -> int:
    state_root = Path(args.state_root).expanduser().resolve()
    launcher = Path(args.launcher).expanduser().resolve()
    plist = Path(args.plist).expanduser().resolve()
    domain = f"gui/{os.getuid()}"
    run_command(["/bin/launchctl", "bootout", domain, str(plist)], timeout=10)
    launcher.unlink(missing_ok=True)
    plist.unlink(missing_ok=True)
    if args.purge_state and state_root.exists():
        shutil.rmtree(state_root)
    print(f"uninstalled: {LABEL}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "check", "status", "doctor", "resume"):
        subparsers.add_parser(name)
    install = subparsers.add_parser("install")
    install.add_argument("--source-cron-id", required=True)
    install.add_argument("--repo-root", default=str(ROOT))
    install.add_argument("--remote-host", default=DEFAULT_REMOTE_HOST)
    install.add_argument("--remote-app", default=DEFAULT_REMOTE_APP)
    install.add_argument("--ssh-key", default=str(Path.home() / ".ssh/id_ed25519_sage_deploy"))
    install.add_argument("--env-file", default=DEFAULT_ENV_FILE)
    install.add_argument("--docker-host", default=DEFAULT_DOCKER_HOST)
    install.add_argument("--health-url", default=DEFAULT_HEALTH_URL)
    install.add_argument("--public-health-url", default=DEFAULT_PUBLIC_HEALTH_URL)
    install.add_argument("--host-key-alias", default=DEFAULT_HOST_KEY_ALIAS)
    install.add_argument("--github-repo", default=DEFAULT_GITHUB_REPO)
    install.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT))
    install.add_argument("--launcher", default=str(DEFAULT_LAUNCHER))
    install.add_argument("--plist", default=str(DEFAULT_PLIST))
    install.add_argument("--interval-seconds", type=int, default=900)
    install.add_argument("--disable-auto-deploy", action="store_true")
    install.add_argument("--cc-connect-bin", default=shutil.which("cc-connect") or "cc-connect")
    install.add_argument("--no-load", action="store_true", help=argparse.SUPPRESS)
    uninstall = subparsers.add_parser("uninstall")
    uninstall.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT))
    uninstall.add_argument("--launcher", default=str(DEFAULT_LAUNCHER))
    uninstall.add_argument("--plist", default=str(DEFAULT_PLIST))
    uninstall.add_argument("--purge-state", action="store_true")
    return parser


def load_controller(args: argparse.Namespace) -> CanaryController:
    config_path = args.config.expanduser().resolve() if args.config else DEFAULT_STATE_ROOT / "config.json"
    values = _load_json(config_path)
    if not values:
        values = {"repo_root": str(ROOT), "state_root": str(config_path.parent)}
    return CanaryController(config_from_values(values))


def status_command(config: CanaryConfig) -> int:
    state = load_state(config.state_path)
    launchd = run_command(["/bin/launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], timeout=5)
    state["launchd_loaded"] = launchd.returncode == 0
    state["auto_deploy"] = config.auto_deploy
    print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def resume_command(config: CanaryConfig) -> int:
    state = load_state(config.state_path)
    state.update(
        {
            "auto_deploy_paused": False,
            "consecutive_sync_failures": 0,
            "last_sync_error": None,
            "last_notification": "deployment-resumed",
        }
    )
    _write_json(config.state_path, state)
    print(json.dumps({"status": "resumed"}, ensure_ascii=False))
    return 0


def main(
    argv: Sequence[str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "install":
            if args.interval_seconds < 300:
                raise CanaryError("可用性巡检间隔不能短于 5 分钟")
            return install_command(args)
        if args.command == "uninstall":
            return uninstall_command(args)
        controller = load_controller(args)
        if args.command == "status":
            return status_command(controller.config)
        if args.command == "resume":
            return resume_command(controller.config)
        if args.command == "doctor":
            validate_config(controller.config)
            print(json.dumps({"status": "ready", "config": str(controller.config.config_path)}, ensure_ascii=False))
            return 0
        if args.command == "check":
            report = controller.check()
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if bool(report["healthy"]) else 1
        lock = _lock_or_skip(controller.config.state_root / "run.lock")
        if lock is None:
            print(json.dumps({"status": "skipped", "reason": "run-active"}))
            return 0
        try:
            result = controller.run_once()
        finally:
            os.close(lock)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "ok" else 1
    except CanaryError as exc:
        print(f"canaryctl blocked: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
