"""PublishedPackage lifecycle, rollback, and fail-closed coverage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from public_agent.registry import (
    PublishedPackageError,
    PublishedPackageProvider,
    PublishedPackageRegistry,
)

PACKAGE = Path("data/public/sage-public-v1.json")


def _payload(revision: str, *, suffix: str = "") -> dict:
    payload = json.loads(PACKAGE.read_text(encoding="utf-8"))
    payload["revision"] = revision
    payload["documents"][0]["revision"] = revision
    if suffix:
        content = payload["documents"][0]["content"] + suffix
        payload["documents"][0]["content"] = content
        payload["documents"][0]["content_sha256"] = hashlib.sha256(content.encode()).hexdigest()
    return payload


def test_bootstrap_stages_and_activates_an_immutable_package(tmp_path: Path) -> None:
    registry = PublishedPackageRegistry(tmp_path, clock=lambda: "2026-07-24T00:00:00+00:00")

    result = registry.bootstrap(PACKAGE, actor="root")

    assert result["status"] == "activated"
    assert result["active_revision"] == "2026-07-24.2"
    assert PublishedPackageProvider(tmp_path).current().revision == "2026-07-24.2"
    state = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    assert [event["action"] for event in state["events"]] == ["staged", "activated"]
    assert (tmp_path / "packages/sage-public/2026-07-24.2.json").stat().st_mode & 0o777 == 0o644


def test_publish_switches_active_revision_and_provider_reloads_without_restart(
    tmp_path: Path,
) -> None:
    registry = PublishedPackageRegistry(tmp_path)
    registry.bootstrap(PACKAGE, actor="root")
    provider = PublishedPackageProvider(tmp_path)
    assert provider.current().revision == "2026-07-24.2"
    registry.stage_payload(_payload("2026-07-24.3", suffix=" P3"), actor="sage-deploy")

    result = registry.activate(
        "sage-public",
        "2026-07-24.3",
        expected_active_revision="2026-07-24.2",
        actor="sage-deploy",
    )

    assert result["active_revision"] == "2026-07-24.3"
    assert provider.current().revision == "2026-07-24.3"
    states = {item["revision"]: item["state"] for item in registry.status()["packages"]}
    assert states == {"2026-07-24.2": "inactive", "2026-07-24.3": "active"}


def test_revoke_active_revision_atomically_restores_previous_healthy_revision(
    tmp_path: Path,
) -> None:
    registry = PublishedPackageRegistry(tmp_path)
    registry.bootstrap(PACKAGE, actor="root")
    registry.stage_payload(_payload("2026-07-24.3", suffix=" P3"), actor="sage-deploy")
    registry.activate(
        "sage-public",
        "2026-07-24.3",
        expected_active_revision="2026-07-24.2",
        actor="sage-deploy",
    )

    result = registry.revoke(
        "sage-public",
        "2026-07-24.3",
        expected_active_revision="2026-07-24.3",
        actor="sage-deploy",
        reason="资料需要重新审核",
    )

    assert result["status"] == "revoked"
    assert result["replacement_revision"] == "2026-07-24.2"
    assert result["active_revision"] == "2026-07-24.2"
    assert PublishedPackageProvider(tmp_path).current().revision == "2026-07-24.2"
    state = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    assert state["events"][-1]["reason"] == "资料需要重新审核"
    assert state["events"][-1]["replacement_revision"] == "2026-07-24.2"


def test_revoke_only_rolls_back_to_the_same_package_id(tmp_path: Path) -> None:
    registry = PublishedPackageRegistry(tmp_path)
    registry.bootstrap(PACKAGE, actor="root")
    other = _payload("2026-07-23.other", suffix=" other package")
    other["package_id"] = "other-public"
    registry.stage_payload(other, actor="root")
    registry.activate(
        "other-public",
        "2026-07-23.other",
        expected_active_revision="2026-07-24.2",
        actor="root",
    )
    registry.stage_payload(_payload("2026-07-24.3", suffix=" P3"), actor="root")
    registry.activate(
        "sage-public",
        "2026-07-24.3",
        expected_active_revision="2026-07-23.other",
        actor="root",
    )

    result = registry.revoke(
        "sage-public",
        "2026-07-24.3",
        expected_active_revision="2026-07-24.3",
        actor="root",
        reason="回退同一资料包",
    )

    assert result["replacement_revision"] == "2026-07-24.2"
    assert PublishedPackageProvider(tmp_path).current().package_id == "sage-public"


def test_revoke_only_active_revision_is_rejected_without_losing_service(tmp_path: Path) -> None:
    registry = PublishedPackageRegistry(tmp_path)
    registry.bootstrap(PACKAGE, actor="root")

    with pytest.raises(PublishedPackageError, match="没有可回退"):
        registry.revoke(
            "sage-public",
            "2026-07-24.2",
            expected_active_revision="2026-07-24.2",
            actor="sage-deploy",
            reason="错误发布",
        )

    assert PublishedPackageProvider(tmp_path).current().revision == "2026-07-24.2"


def test_same_revision_with_different_content_cannot_overwrite_immutable_file(
    tmp_path: Path,
) -> None:
    registry = PublishedPackageRegistry(tmp_path)
    registry.bootstrap(PACKAGE, actor="root")

    with pytest.raises(PublishedPackageError, match="禁止覆盖"):
        registry.stage_payload(_payload("2026-07-24.2", suffix=" changed"), actor="root")


def test_publish_uses_compare_and_swap_for_the_active_revision(tmp_path: Path) -> None:
    registry = PublishedPackageRegistry(tmp_path)
    registry.bootstrap(PACKAGE, actor="root")
    registry.stage_payload(_payload("2026-07-24.3", suffix=" P3"), actor="root")

    with pytest.raises(PublishedPackageError, match="active revision 已变化"):
        registry.activate(
            "sage-public",
            "2026-07-24.3",
            expected_active_revision="stale",
            actor="root",
        )

    assert PublishedPackageProvider(tmp_path).current().revision == "2026-07-24.2"


def test_corrupted_active_package_fails_closed(tmp_path: Path) -> None:
    registry = PublishedPackageRegistry(tmp_path)
    registry.bootstrap(PACKAGE, actor="root")
    active = tmp_path / "packages/sage-public/2026-07-24.2.json"
    active.write_text("{}", encoding="utf-8")

    with pytest.raises(PublishedPackageError, match="校验失败"):
        PublishedPackageProvider(tmp_path).current()


@pytest.mark.parametrize("unsafe", ["../escape", "/absolute", "with space", ""])
def test_package_references_cannot_escape_registry_root(tmp_path: Path, unsafe: str) -> None:
    registry = PublishedPackageRegistry(tmp_path)
    payload = _payload("2026-07-24.3")
    payload["revision"] = unsafe

    with pytest.raises(PublishedPackageError, match="(revision 格式无效|校验失败)"):
        registry.stage_payload(payload, actor="root")
