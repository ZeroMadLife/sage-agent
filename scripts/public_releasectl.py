#!/usr/bin/env python3
"""Root-owned, bounded release controller for the public Sage facade."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

_UTC = timezone.utc  # noqa: UP017 - the ECS host still runs Python 3.10.
COMMIT_TAG = re.compile(r"[0-9a-f]{40}")
IMAGE_REPOSITORY = "sage-public"
LIVE_CONTAINER = "sage-public-gateway"
PREVIOUS_CONTAINER = "sage-public-gateway-previous"
CANDIDATE_CONTAINER = "sage-public-gateway-candidate"
PUBLIC_NETWORK = "sage-public-release"
DEFAULT_SOURCE_DOCKER_HOST = "unix:///run/user/1002/docker.sock"
DEFAULT_TARGET_DOCKER_HOST = "unix:///var/run/docker.sock"
DEFAULT_STATE_FILE = Path("/var/lib/sage-public-release/state.json")
DEFAULT_LOCK_FILE = Path("/run/lock/sage-public-release.lock")
DEFAULT_CANDIDATE_URL = "http://127.0.0.1:18081/"
DEFAULT_LIVE_URL = "http://127.0.0.1/"


class PublicReleaseError(RuntimeError):
    """A bounded public release operation failed."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class PublicReleaseConfig:
    source_docker_host: str = DEFAULT_SOURCE_DOCKER_HOST
    target_docker_host: str = DEFAULT_TARGET_DOCKER_HOST
    state_file: Path = DEFAULT_STATE_FILE
    lock_file: Path = DEFAULT_LOCK_FILE
    candidate_url: str = DEFAULT_CANDIDATE_URL
    live_url: str = DEFAULT_LIVE_URL


Runner = Callable[[Sequence[str], int], CommandResult]
HttpProbe = Callable[[str], bool]


def run_command(command: Sequence[str], timeout: int = 120) -> CommandResult:
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CommandResult(124, stderr=type(exc).__name__)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def validate_tag(value: str) -> str:
    if COMMIT_TAG.fullmatch(value) is None:
        raise PublicReleaseError("发布版本必须是 40 位小写 Git commit SHA")
    return value


def parse_request(stream: TextIO) -> tuple[str, str | None]:
    raw = stream.read(2049)
    if len(raw) > 2048:
        raise PublicReleaseError("发布请求超过 2 KiB")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PublicReleaseError("发布请求不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise PublicReleaseError("发布请求必须是 JSON object")
    action = value.get("action")
    allowed = {"status": {"action"}, "apply": {"action", "tag"}, "rollback": {"action", "tag"}}
    if not isinstance(action, str) or action not in allowed or set(value) != allowed[action]:
        raise PublicReleaseError("发布请求 action 或字段无效")
    tag = None if action == "status" else validate_tag(str(value["tag"]))
    return str(action), tag


def probe_http(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            body = response.read(4096)
            return response.status == 200 and b"<title>" in body and b"Sage" in body
    except OSError:
        return False


class PublicReleaseController:
    def __init__(
        self,
        config: PublicReleaseConfig,
        *,
        runner: Runner = run_command,
        http_probe: HttpProbe = probe_http,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.config = config
        self.runner = runner
        self.http_probe = http_probe
        self.clock = clock or (lambda: datetime.now(_UTC).isoformat())

    def _docker(self, host: str, *args: str, timeout: int = 120) -> CommandResult:
        result = self.runner(["docker", "--host", host, *args], timeout)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[-600:]
            raise PublicReleaseError("Docker 操作失败" + (f": {detail}" if detail else ""))
        return result

    def _target(self, *args: str, timeout: int = 120) -> CommandResult:
        return self._docker(self.config.target_docker_host, *args, timeout=timeout)

    @staticmethod
    def _container_args(name: str, image: str, binding: str) -> list[str]:
        return [
            "run",
            "-d",
            "--name",
            name,
            "--restart",
            "unless-stopped" if name == LIVE_CONTAINER else "no",
            "--publish",
            binding,
            "--network",
            PUBLIC_NETWORK,
            "--user",
            "65532:65532",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--tmpfs",
            "/config:rw,noexec,nosuid,mode=1777,size=8m",
            "--tmpfs",
            "/data:rw,noexec,nosuid,mode=1777,size=8m",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,mode=1777,size=8m",
            "--health-cmd",
            "wget -qO- http://127.0.0.1:8081/ >/dev/null || exit 1",
            "--health-interval",
            "15s",
            "--health-timeout",
            "5s",
            "--health-retries",
            "3",
            image,
        ]

    def _inspect_optional(self, name: str) -> bool:
        result = self.runner(
            ["docker", "--host", self.config.target_docker_host, "container", "inspect", name],
            30,
        )
        return result.returncode == 0

    def _container_tag(self, name: str) -> str | None:
        result = self.runner(
            [
                "docker",
                "--host",
                self.config.target_docker_host,
                "container",
                "inspect",
                "--format",
                "{{.Config.Image}}",
                name,
            ],
            30,
        )
        if result.returncode != 0:
            return None
        image = result.stdout.strip()
        prefix = f"{IMAGE_REPOSITORY}:"
        if not image.startswith(prefix):
            return None
        tag = image.removeprefix(prefix)
        return tag if COMMIT_TAG.fullmatch(tag) else None

    def _remove_optional(self, name: str) -> None:
        if self._inspect_optional(name):
            self._target("container", "rm", "--force", name, timeout=60)

    def _ensure_network(self) -> None:
        result = self.runner(
            [
                "docker",
                "--host",
                self.config.target_docker_host,
                "network",
                "inspect",
                "--format",
                "{{.Internal}} {{.Driver}}",
                PUBLIC_NETWORK,
            ],
            30,
        )
        if result.returncode == 0:
            if result.stdout.strip() != "false bridge":
                raise PublicReleaseError("公共门面 Docker 网络安全属性不匹配")
            return
        self._target("network", "create", "--driver", "bridge", PUBLIC_NETWORK)

    def _wait_healthy(self, url: str, attempts: int = 20) -> None:
        for _ in range(attempts):
            if self.http_probe(url):
                return
            time.sleep(0.25)
        raise PublicReleaseError("公共门面健康检查失败")

    def _load_state(self) -> dict[str, object]:
        if not self.config.state_file.is_file():
            return {}
        try:
            value = json.loads(self.config.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PublicReleaseError("公共发布状态文件损坏") from exc
        if not isinstance(value, dict):
            raise PublicReleaseError("公共发布状态文件格式无效")
        return value

    def _write_state(self, value: Mapping[str, object]) -> None:
        self.config.state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.config.state_file.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.config.state_file)

    def _verify_image(self, host: str, tag: str) -> str:
        image = f"{IMAGE_REPOSITORY}:{validate_tag(tag)}"
        result = self._docker(
            host,
            "image",
            "inspect",
            "--format",
            "{{index .Config.Labels \"org.opencontainers.image.revision\"}} {{.Config.User}}",
            image,
            timeout=30,
        )
        if result.stdout.strip() != f"{tag} 65532:65532":
            raise PublicReleaseError("公共镜像 revision 或运行用户不匹配")
        return image

    def _import_image(self, tag: str) -> str:
        image = self._verify_image(self.config.source_docker_host, tag)
        with tempfile.NamedTemporaryFile(prefix="sage-public-", suffix=".tar", delete=False) as stream:
            archive = Path(stream.name)
        try:
            os.chmod(archive, 0o600)
            saved = self.runner(
                [
                    "docker",
                    "--host",
                    self.config.source_docker_host,
                    "image",
                    "save",
                    "--output",
                    str(archive),
                    image,
                ],
                600,
            )
            if saved.returncode != 0:
                raise PublicReleaseError("导出公共镜像失败")
            self._target("image", "load", "--input", str(archive), timeout=600)
        finally:
            archive.unlink(missing_ok=True)
        return self._verify_image(self.config.target_docker_host, tag)

    def _run_candidate(self, image: str) -> None:
        self._remove_optional(CANDIDATE_CONTAINER)
        self._target(*self._container_args(CANDIDATE_CONTAINER, image, "127.0.0.1:18081:8081"))
        try:
            self._wait_healthy(self.config.candidate_url)
        finally:
            self._remove_optional(CANDIDATE_CONTAINER)

    def _start_live(self, image: str) -> None:
        self._target(*self._container_args(LIVE_CONTAINER, image, "80:8081"))
        self._wait_healthy(self.config.live_url)

    def _restore_previous(self) -> None:
        self._remove_optional(LIVE_CONTAINER)
        if not self._inspect_optional(PREVIOUS_CONTAINER):
            raise PublicReleaseError("公共门面切换失败且没有可恢复容器")
        self._target("container", "rename", PREVIOUS_CONTAINER, LIVE_CONTAINER)
        self._target("container", "start", LIVE_CONTAINER)
        self._wait_healthy(self.config.live_url)

    def apply(self, tag: str) -> dict[str, object]:
        tag = validate_tag(tag)
        self._ensure_network()
        state = self._load_state()
        current = str(state.get("current") or "") or self._container_tag(LIVE_CONTAINER)
        if current == tag and self.http_probe(self.config.live_url):
            if state.get("current") != tag:
                self._write_state(
                    {"current": tag, "previous": None, "deployed_at": self.clock()}
                )
            return {"status": "up-to-date", "tag": tag}
        image = self._import_image(tag)
        self._run_candidate(image)
        previous = current
        self._remove_optional(PREVIOUS_CONTAINER)
        try:
            if self._inspect_optional(LIVE_CONTAINER):
                self._target("container", "stop", LIVE_CONTAINER, timeout=60)
                self._target("container", "rename", LIVE_CONTAINER, PREVIOUS_CONTAINER)
            self._start_live(image)
        except Exception:
            if self._inspect_optional(PREVIOUS_CONTAINER):
                self._restore_previous()
            elif self._inspect_optional(LIVE_CONTAINER):
                self._target("container", "start", LIVE_CONTAINER)
            raise
        deployed_at = self.clock()
        self._write_state(
            {"current": tag, "previous": previous, "deployed_at": deployed_at}
        )
        return {"status": "deployed", "tag": tag, "previous": previous, "deployed_at": deployed_at}

    def rollback(self, tag: str) -> dict[str, object]:
        tag = validate_tag(tag)
        self._ensure_network()
        state = self._load_state()
        current = str(state.get("current") or "") or self._container_tag(LIVE_CONTAINER)
        if self._container_tag(PREVIOUS_CONTAINER) == tag:
            self._restore_previous()
            rolled_back_at = self.clock()
            self._write_state(
                {"current": tag, "previous": current, "rolled_back_at": rolled_back_at}
            )
            return {
                "status": "rolled-back",
                "tag": tag,
                "previous": current,
                "rolled_back_at": rolled_back_at,
            }
        image = self._verify_image(self.config.target_docker_host, tag)
        self._run_candidate(image)
        self._remove_optional(PREVIOUS_CONTAINER)
        try:
            if self._inspect_optional(LIVE_CONTAINER):
                self._target("container", "stop", LIVE_CONTAINER, timeout=60)
                self._target("container", "rename", LIVE_CONTAINER, PREVIOUS_CONTAINER)
            self._start_live(image)
        except Exception:
            if self._inspect_optional(PREVIOUS_CONTAINER):
                self._restore_previous()
            elif self._inspect_optional(LIVE_CONTAINER):
                self._target("container", "start", LIVE_CONTAINER)
            raise
        rolled_back_at = self.clock()
        self._write_state(
            {"current": tag, "previous": current, "rolled_back_at": rolled_back_at}
        )
        return {"status": "rolled-back", "tag": tag, "previous": current, "rolled_back_at": rolled_back_at}

    def status(self) -> dict[str, object]:
        state = self._load_state()
        current = str(state.get("current") or "") or self._container_tag(LIVE_CONTAINER)
        return {
            "status": "healthy" if self.http_probe(self.config.live_url) else "degraded",
            "current": current,
            "previous": state.get("previous"),
            "deployed_at": state.get("deployed_at", state.get("rolled_back_at")),
            "container": LIVE_CONTAINER if self._inspect_optional(LIVE_CONTAINER) else None,
        }

    def execute(self, action: str, tag: str | None) -> dict[str, object]:
        self.config.lock_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.config.lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(descriptor)
            raise PublicReleaseError("已有公共发布正在执行") from exc
        try:
            if action == "status":
                return self.status()
            if action == "apply" and tag is not None:
                return self.apply(tag)
            if action == "rollback" and tag is not None:
                return self.rollback(tag)
            raise PublicReleaseError("发布请求无效")
        finally:
            os.close(descriptor)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-docker-host", default=DEFAULT_SOURCE_DOCKER_HOST)
    parser.add_argument("--target-docker-host", default=DEFAULT_TARGET_DOCKER_HOST)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK_FILE)
    parser.add_argument("--candidate-url", default=DEFAULT_CANDIDATE_URL)
    parser.add_argument("--live-url", default=DEFAULT_LIVE_URL)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        action, tag = parse_request(sys.stdin)
        result = PublicReleaseController(
            PublicReleaseConfig(
                source_docker_host=args.source_docker_host,
                target_docker_host=args.target_docker_host,
                state_file=args.state_file.resolve(),
                lock_file=args.lock_file.resolve(),
                candidate_url=args.candidate_url,
                live_url=args.live_url,
            )
        ).execute(action, tag)
    except PublicReleaseError as exc:
        print(f"public-releasectl blocked: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
