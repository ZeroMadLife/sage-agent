"""Tests for the bounded root-owned public facade release controller."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from scripts.public_releasectl import (
    CANDIDATE_CONTAINER,
    LIVE_CONTAINER,
    PREVIOUS_CONTAINER,
    CommandResult,
    PublicReleaseConfig,
    PublicReleaseController,
    PublicReleaseError,
    parse_request,
    validate_tag,
)

SHA = "a" * 40
NEXT_SHA = "b" * 40


def _config(tmp_path: Path) -> PublicReleaseConfig:
    return PublicReleaseConfig(
        source_docker_host="unix:///run/user/1002/docker.sock",
        target_docker_host="unix:///var/run/docker.sock",
        state_file=tmp_path / "state.json",
        lock_file=tmp_path / "release.lock",
        candidate_url="http://127.0.0.1:18081/",
        live_url="http://127.0.0.1/",
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


class FakeDocker:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.containers: dict[str, str] = {LIVE_CONTAINER: f"sage-public:{SHA}"}
        self.images = {SHA, NEXT_SHA}
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
        if args[:2] == ["image", "save"]:
            Path(args[args.index("--output") + 1]).write_bytes(b"image")
            return CommandResult(0)
        if args[:2] == ["image", "load"]:
            return CommandResult(0)
        if args and args[0] == "run":
            name = args[args.index("--name") + 1]
            image = args[-1]
            if name == LIVE_CONTAINER and self.fail_live:
                return CommandResult(1, stderr="port failure")
            self.containers[name] = image
            return CommandResult(0, "container-id")
        raise AssertionError((host, args))


def test_existing_manual_gateway_is_adopted_without_redeployment(tmp_path: Path) -> None:
    docker = FakeDocker()
    controller = PublicReleaseController(
        _config(tmp_path), runner=docker, http_probe=lambda _url: True, clock=lambda: "now"
    )

    result = controller.apply(SHA)

    assert result == {"status": "up-to-date", "tag": SHA}
    assert json.loads((_config(tmp_path).state_file).read_text(encoding="utf-8")) == {
        "current": SHA,
        "previous": None,
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
        _config(tmp_path), runner=docker, http_probe=probe, clock=lambda: "now"
    )
    result = controller.apply(NEXT_SHA)

    assert result == {
        "status": "deployed",
        "tag": NEXT_SHA,
        "previous": SHA,
        "deployed_at": "now",
    }
    assert probes == ["http://127.0.0.1:18081/", "http://127.0.0.1/"]
    assert docker.containers[LIVE_CONTAINER] == f"sage-public:{NEXT_SHA}"
    assert docker.containers[PREVIOUS_CONTAINER] == f"sage-public:{SHA}"
    assert CANDIDATE_CONTAINER not in docker.containers
    image_inspects = [call for call in docker.calls if call[3:5] == ["image", "inspect"]]
    assert image_inspects
    assert all(call.count("--format") == 1 for call in image_inspects)
    state = json.loads((_config(tmp_path).state_file).read_text(encoding="utf-8"))
    assert state["current"] == NEXT_SHA
    assert state["previous"] == SHA


def test_failed_live_switch_restores_previous_container(tmp_path: Path) -> None:
    docker = FakeDocker()
    docker.fail_live = True
    controller = PublicReleaseController(
        _config(tmp_path), runner=docker, http_probe=lambda _url: True
    )

    with pytest.raises(PublicReleaseError):
        controller.apply(NEXT_SHA)

    assert docker.containers[LIVE_CONTAINER] == f"sage-public:{SHA}"
    assert PREVIOUS_CONTAINER not in docker.containers
    assert not _config(tmp_path).state_file.exists()


def test_rollback_restores_retained_legacy_previous_without_oci_label(
    tmp_path: Path,
) -> None:
    docker = FakeDocker()
    docker.containers = {
        LIVE_CONTAINER: f"sage-public:{NEXT_SHA}",
        PREVIOUS_CONTAINER: f"sage-public:{SHA}",
    }
    _config(tmp_path).state_file.write_text(
        json.dumps({"current": NEXT_SHA, "previous": SHA}), encoding="utf-8"
    )
    docker.images.remove(SHA)
    controller = PublicReleaseController(
        _config(tmp_path), runner=docker, http_probe=lambda _url: True, clock=lambda: "now"
    )

    result = controller.rollback(SHA)

    assert result == {
        "status": "rolled-back",
        "tag": SHA,
        "previous": NEXT_SHA,
        "rolled_back_at": "now",
    }
    assert docker.containers[LIVE_CONTAINER] == f"sage-public:{SHA}"
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
