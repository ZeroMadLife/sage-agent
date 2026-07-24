"""Tests for the bounded root-owned public facade release controller."""

from __future__ import annotations

import io
import json
import os
import urllib.request
from pathlib import Path

import pytest

from scripts.public_releasectl import (
    AGENT_IMAGE_REPOSITORY,
    AGENT_PACKAGE_REGISTRY_CONTAINER_PATH,
    CANDIDATE_AGENT_CONTAINER,
    CANDIDATE_CONTAINER,
    LIVE_AGENT_CONTAINER,
    LIVE_CONTAINER,
    PREVIOUS_AGENT_CONTAINER,
    PREVIOUS_CONTAINER,
    PUBLIC_BIND_ADDRESS,
    PUBLIC_CONFIG_VOLUME,
    PUBLIC_DATA_VOLUME,
    CommandResult,
    PublicReleaseConfig,
    PublicReleaseController,
    PublicReleaseError,
    parse_request,
    probe_public_api,
    validate_tag,
)

SHA = "a" * 40
NEXT_SHA = "b" * 40


def _config(tmp_path: Path) -> PublicReleaseConfig:
    env_file = tmp_path / "public-agent.env"
    env_file.write_text("SAGE_PUBLIC_AGENT_API_KEY=test\n", encoding="utf-8")
    env_file.chmod(0o600)
    package_registry = tmp_path / "packages"
    package_registry.mkdir(exist_ok=True)
    (package_registry / "registry.json").write_text("{}\n", encoding="utf-8")
    return PublicReleaseConfig(
        source_docker_host="unix:///run/user/1002/docker.sock",
        target_docker_host="unix:///var/run/docker.sock",
        state_file=tmp_path / "state.json",
        lock_file=tmp_path / "release.lock",
        candidate_url="http://127.0.0.1:18081/",
        candidate_agent_url="http://127.0.0.1:18083/healthz",
        candidate_api_url="http://127.0.0.1:18081/api/public/v1/ask",
        live_url="http://127.0.0.1:18082/healthz",
        agent_env_file=env_file,
        agent_env_owner_uid=os.getuid(),
        agent_budget_state_file=tmp_path / "agent-budget.json",
        agent_package_registry_root=package_registry,
        agent_package_registry_owner_uid=os.getuid(),
        agent_runtime_uid=os.getuid(),
    )


@pytest.mark.parametrize("tag", ["main", "A" * 40, "a" * 39, "a" * 41, "a" * 40 + ";id"])
def test_tag_requires_a_full_lowercase_commit_sha(tag: str) -> None:
    with pytest.raises(PublicReleaseError):
        validate_tag(tag)


def test_request_is_bounded_json_with_exact_fields() -> None:
    assert parse_request(io.StringIO('{"action":"apply","tag":"' + SHA + '"}')) == (
        "apply",
        SHA,
    )
    assert parse_request(io.StringIO('{"action":"status"}')) == ("status", None)

    with pytest.raises(PublicReleaseError):
        parse_request(io.StringIO('{"action":"apply","tag":"' + SHA + '","shell":"id"}'))
    with pytest.raises(PublicReleaseError):
        parse_request(io.StringIO("{" + "x" * 2100 + "}"))


def test_public_api_probe_verifies_the_minimum_identity_question(monkeypatch) -> None:
    captured: list[urllib.request.Request] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        @staticmethod
        def read(_limit: int) -> bytes:
            return json.dumps(
                {
                    "status": "answered",
                    "citations": [{"document_id": "sage-identity"}],
                    "receipt": {"package_revision": "2026-07-24.3"},
                }
            ).encode()

    def urlopen(request: urllib.request.Request, *, timeout: int):
        assert timeout == 25
        captured.append(request)
        return Response()

    monkeypatch.setattr("scripts.public_releasectl.urllib.request.urlopen", urlopen)

    assert probe_public_api("http://127.0.0.1:18081/api/public/v1/ask") is True
    assert len(captured) == 1
    assert json.loads(captured[0].data or b"{}")["question"] == "你是谁？"


class FakeDocker:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.containers: dict[str, str] = {
            LIVE_CONTAINER: f"sage-public:{SHA}",
            LIVE_AGENT_CONTAINER: f"{AGENT_IMAGE_REPOSITORY}:{SHA}",
        }
        self.images = {SHA, NEXT_SHA}
        self.volumes = {PUBLIC_DATA_VOLUME, PUBLIC_CONFIG_VOLUME}
        self.fail_live = False

    def __call__(self, command, timeout):
        command = list(command)
        self.calls.append(command)
        host = command[2]
        args = command[3:]
        if args[:3] == ["image", "inspect", "--format"]:
            tag = args[-1].split(":", 1)[1]
            if tag not in self.images:
                return CommandResult(1, stderr="missing")
            return CommandResult(0, f"{tag} 65532:65532\n")
        if args[:3] == ["container", "inspect", "--format"]:
            name = args[-1]
            image = self.containers.get(name)
            return CommandResult(0, f"{image}\n") if image else CommandResult(1)
        if args[:3] == ["network", "inspect", "--format"]:
            return CommandResult(0, "false bridge\n")
        if args[:3] == ["volume", "inspect", "--format"]:
            if args[-1] in self.volumes:
                return CommandResult(0, "local\n")
            return CommandResult(1)
        if args[:2] == ["volume", "create"]:
            self.volumes.add(args[-1])
            return CommandResult(0, f"{args[-1]}\n")
        if args[:2] == ["container", "inspect"]:
            return CommandResult(0) if args[-1] in self.containers else CommandResult(1)
        if args[:3] == ["container", "rm", "--force"]:
            self.containers.pop(args[-1], None)
            return CommandResult(0)
        if args[:2] == ["container", "stop"]:
            return CommandResult(0)
        if args[:2] == ["container", "rename"]:
            old, new = args[-2:]
            self.containers[new] = self.containers.pop(old)
            return CommandResult(0)
        if args[:2] == ["container", "start"]:
            return CommandResult(0)
        if args[:2] == ["container", "exec"]:
            return CommandResult(0)
        if args[:2] == ["image", "save"]:
            Path(args[args.index("--output") + 1]).write_bytes(b"image")
            return CommandResult(0)
        if args[:2] == ["image", "load"]:
            return CommandResult(0)
        if args and args[0] == "run":
            name = args[args.index("--name") + 1]
            image = next(
                arg
                for arg in args
                if arg.startswith(("sage-public:", f"{AGENT_IMAGE_REPOSITORY}:"))
            )
            if name == LIVE_CONTAINER and self.fail_live:
                return CommandResult(1, stderr="port failure")
            self.containers[name] = image
            return CommandResult(0, "container-id")
        raise AssertionError((host, args))


def test_existing_p2_pair_is_adopted_without_redeployment(tmp_path: Path) -> None:
    docker = FakeDocker()
    controller = PublicReleaseController(
        _config(tmp_path),
        runner=docker,
        http_probe=lambda _url: True,
        agent_probe=lambda _url: True,
        api_probe=lambda _url: True,
        clock=lambda: "now",
    )

    result = controller.apply(SHA)

    assert result == {"status": "up-to-date", "tag": SHA}
    assert json.loads((_config(tmp_path).state_file).read_text(encoding="utf-8")) == {
        "current": SHA,
        "previous": None,
        "agent_enabled": True,
        "deployed_at": "now",
    }
    assert not any("save" in call for call in docker.calls)


def test_apply_verifies_candidate_then_atomically_switches_live(tmp_path: Path) -> None:
    docker = FakeDocker()
    probes: list[str] = []

    def probe(url: str) -> bool:
        probes.append(url)
        return True

    controller = PublicReleaseController(
        _config(tmp_path),
        runner=docker,
        http_probe=probe,
        agent_probe=probe,
        api_probe=probe,
        clock=lambda: "now",
    )
    result = controller.apply(NEXT_SHA)

    assert result == {
        "status": "deployed",
        "tag": NEXT_SHA,
        "previous": SHA,
        "deployed_at": "now",
    }
    assert probes == [
        "http://127.0.0.1:18083/healthz",
        "http://127.0.0.1:18081/api/public/v1/ask",
        "http://127.0.0.1:18081/",
        "http://127.0.0.1:18082/healthz",
    ]
    assert docker.containers[LIVE_CONTAINER] == f"sage-public:{NEXT_SHA}"
    assert docker.containers[PREVIOUS_CONTAINER] == f"sage-public:{SHA}"
    assert docker.containers[LIVE_AGENT_CONTAINER] == f"{AGENT_IMAGE_REPOSITORY}:{NEXT_SHA}"
    assert docker.containers[PREVIOUS_AGENT_CONTAINER] == f"{AGENT_IMAGE_REPOSITORY}:{SHA}"
    assert CANDIDATE_CONTAINER not in docker.containers
    assert CANDIDATE_AGENT_CONTAINER not in docker.containers
    image_inspects = [call for call in docker.calls if call[3:5] == ["image", "inspect"]]
    assert image_inspects
    assert all(call.count("--format") == 1 for call in image_inspects)
    state = json.loads((_config(tmp_path).state_file).read_text(encoding="utf-8"))
    assert state["current"] == NEXT_SHA
    assert state["previous"] == SHA
    assert state["agent_enabled"] is True

    runs = [call[3:] for call in docker.calls if call[3:4] == ["run"]]
    candidate = next(call for call in runs if CANDIDATE_CONTAINER in call)
    live = next(call for call in runs if LIVE_CONTAINER in call)
    candidate_agent = next(call for call in runs if CANDIDATE_AGENT_CONTAINER in call)
    live_agent = next(call for call in runs if LIVE_AGENT_CONTAINER in call)
    assert "127.0.0.1:18081:8081" in candidate
    assert "/config:rw,noexec,nosuid,mode=1777,size=8m" in candidate
    assert "/data:rw,noexec,nosuid,mode=1777,size=8m" in candidate
    assert candidate[-6:] == [
        "caddy",
        "run",
        "--config",
        "/etc/caddy/Caddyfile.candidate",
        "--adapter",
        "caddyfile",
    ]
    assert "127.0.0.1:18082:8081" in live
    assert f"{PUBLIC_BIND_ADDRESS}:80:8081" in live
    assert f"{PUBLIC_BIND_ADDRESS}:443:8443" in live
    assert f"type=volume,source={PUBLIC_DATA_VOLUME},target=/data" in live
    assert f"type=volume,source={PUBLIC_CONFIG_VOLUME},target=/config" in live
    assert PUBLIC_DATA_VOLUME not in candidate
    assert PUBLIC_CONFIG_VOLUME not in candidate
    assert "127.0.0.1:18083:8082" in candidate_agent
    assert "--env-file" in candidate_agent
    assert "SAGE_PUBLIC_BUDGET_STATE_PATH=/var/lib/sage-public-agent/budget.json" in candidate_agent
    assert any(value.startswith("type=bind,source=") for value in candidate_agent)
    assert (
        f"SAGE_PUBLIC_PACKAGE_REGISTRY={AGENT_PACKAGE_REGISTRY_CONTAINER_PATH}" in candidate_agent
    )
    assert any(
        value.endswith(f"target={AGENT_PACKAGE_REGISTRY_CONTAINER_PATH},readonly")
        for value in candidate_agent
    )
    assert f"SAGE_PUBLIC_AGENT_UPSTREAM={CANDIDATE_AGENT_CONTAINER}:8082" in candidate
    assert not any(value.startswith("0.0.0.0:") for value in live_agent)


def test_live_agent_waits_for_runtime_readiness_before_switching_gateway(
    tmp_path: Path,
) -> None:
    class StartingDocker(FakeDocker):
        def __init__(self) -> None:
            super().__init__()
            self.live_agent_attempts = 0

        def __call__(self, command, timeout):
            args = list(command)[3:]
            if args[:3] == ["container", "exec", LIVE_AGENT_CONTAINER]:
                self.calls.append(list(command))
                self.live_agent_attempts += 1
                return CommandResult(0 if self.live_agent_attempts == 3 else 1)
            return super().__call__(command, timeout)

    docker = StartingDocker()
    controller = PublicReleaseController(
        _config(tmp_path),
        runner=docker,
        http_probe=lambda _url: True,
        agent_probe=lambda _url: True,
        api_probe=lambda _url: True,
    )

    result = controller.apply(NEXT_SHA)

    assert result["status"] == "deployed"
    assert docker.live_agent_attempts == 3
    assert docker.containers[LIVE_CONTAINER] == f"sage-public:{NEXT_SHA}"


def test_failed_live_switch_restores_previous_container(tmp_path: Path) -> None:
    docker = FakeDocker()
    docker.fail_live = True
    controller = PublicReleaseController(
        _config(tmp_path),
        runner=docker,
        http_probe=lambda _url: True,
        agent_probe=lambda _url: True,
        api_probe=lambda _url: True,
    )

    with pytest.raises(PublicReleaseError):
        controller.apply(NEXT_SHA)

    assert docker.containers[LIVE_CONTAINER] == f"sage-public:{SHA}"
    assert docker.containers[LIVE_AGENT_CONTAINER] == f"{AGENT_IMAGE_REPOSITORY}:{SHA}"
    assert PREVIOUS_CONTAINER not in docker.containers
    assert PREVIOUS_AGENT_CONTAINER not in docker.containers
    assert not _config(tmp_path).state_file.exists()


def test_apply_creates_missing_local_certificate_volumes(tmp_path: Path) -> None:
    docker = FakeDocker()
    docker.volumes.clear()
    controller = PublicReleaseController(
        _config(tmp_path),
        runner=docker,
        http_probe=lambda _url: True,
        agent_probe=lambda _url: True,
        api_probe=lambda _url: True,
    )

    controller.apply(NEXT_SHA)

    assert docker.volumes == {PUBLIC_DATA_VOLUME, PUBLIC_CONFIG_VOLUME}


def test_rollback_restores_retained_legacy_previous_without_oci_label(
    tmp_path: Path,
) -> None:
    docker = FakeDocker()
    docker.containers = {
        LIVE_CONTAINER: f"sage-public:{NEXT_SHA}",
        PREVIOUS_CONTAINER: f"sage-public:{SHA}",
        LIVE_AGENT_CONTAINER: f"{AGENT_IMAGE_REPOSITORY}:{NEXT_SHA}",
        PREVIOUS_AGENT_CONTAINER: f"{AGENT_IMAGE_REPOSITORY}:{SHA}",
    }
    _config(tmp_path).state_file.write_text(
        json.dumps({"current": NEXT_SHA, "previous": SHA}), encoding="utf-8"
    )
    docker.images.remove(SHA)
    controller = PublicReleaseController(
        _config(tmp_path),
        runner=docker,
        http_probe=lambda _url: True,
        agent_probe=lambda _url: True,
        api_probe=lambda _url: True,
        clock=lambda: "now",
    )

    result = controller.rollback(SHA)

    assert result == {
        "status": "rolled-back",
        "tag": SHA,
        "previous": NEXT_SHA,
        "rolled_back_at": "now",
    }
    assert docker.containers[LIVE_CONTAINER] == f"sage-public:{SHA}"
    assert docker.containers[LIVE_AGENT_CONTAINER] == f"{AGENT_IMAGE_REPOSITORY}:{SHA}"
    assert not any(call[3:5] == ["image", "inspect"] for call in docker.calls)


def test_image_revision_and_user_are_both_required(tmp_path: Path) -> None:
    def runner(command, timeout):
        if "image" in command and "inspect" in command:
            return CommandResult(0, f"{NEXT_SHA} root\n")
        if "container" in command and "inspect" in command:
            return CommandResult(1)
        if "network" in command and "inspect" in command:
            return CommandResult(0, "false bridge\n")
        raise AssertionError(command)

    controller = PublicReleaseController(_config(tmp_path), runner=runner)

    with pytest.raises(PublicReleaseError, match="revision"):
        controller.apply(NEXT_SHA)


def test_release_rejects_an_existing_network_with_drifted_attributes(tmp_path: Path) -> None:
    def runner(command, timeout):
        if "network" in command and "inspect" in command:
            return CommandResult(0, "true bridge\n")
        raise AssertionError(command)

    controller = PublicReleaseController(_config(tmp_path), runner=runner)

    with pytest.raises(PublicReleaseError, match="网络安全属性"):
        controller.apply(NEXT_SHA)


def test_release_requires_a_root_owned_private_public_agent_env(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.agent_env_file.chmod(0o644)
    controller = PublicReleaseController(
        config,
        runner=FakeDocker(),
        http_probe=lambda _url: True,
        agent_probe=lambda _url: True,
        api_probe=lambda _url: True,
    )

    with pytest.raises(PublicReleaseError, match="0600"):
        controller.apply(NEXT_SHA)


def test_status_keeps_a_legacy_static_release_healthy(tmp_path: Path) -> None:
    docker = FakeDocker()
    docker.containers.pop(LIVE_AGENT_CONTAINER)
    controller = PublicReleaseController(
        _config(tmp_path), runner=docker, http_probe=lambda _url: True
    )

    assert controller.status()["status"] == "healthy"
    assert controller.status()["agent_container"] is None
