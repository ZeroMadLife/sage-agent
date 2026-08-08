"""ToolBundle 运行对象与不可变本轮能力快照的契约测试。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from sage_harness import (
    CapabilityBinding,
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilitySelectionIndex,
    DeferredToolSetup,
)

from core.harness.tool_bundle import ToolBundleSnapshot
from core.harness.tools_adapter import CodingToolBundle


def _deferred_setup() -> DeferredToolSetup:
    descriptor = CapabilityDescriptor(
        capability_id="web:search",
        name="search_web",
        origin="web",
        kind="tool",
        revision="web-r1",
        description="Search public sources",
        surfaces=("coding",),
        risk="low",
        permission="runtime",
        deferred=True,
        remote_content=True,
        availability="available",
        timeout_seconds=30.0,
        tags=("web",),
    )
    index = CapabilitySelectionIndex(
        CapabilityRegistry((descriptor,)),
        bindings=(CapabilityBinding("web:search", "search_web"),),
        surface="coding",
    )
    return DeferredToolSetup(catalog_hash="catalog-r1", selection_index=index)


def _bundle() -> CodingToolBundle:
    return CodingToolBundle(
        tools=(),
        deferred_setup=_deferred_setup(),
        capability_revision="cap-r1",
        capability_ids_by_tool_name={
            "read_file": "local:read_file",
            "search_web": "web:search",
        },
        capability_count=2,
    )


def test_snapshot_projects_only_immutable_catalog_metadata() -> None:
    snapshot = ToolBundleSnapshot.from_runtime_bundle(_bundle())

    assert snapshot.catalog_hash == "catalog-r1"
    assert snapshot.capability_revision == "cap-r1"
    assert snapshot.resident_ids == ("local:read_file",)
    assert snapshot.deferred_ids == ("web:search",)
    assert snapshot.skill_scope_active is False
    assert snapshot.skill_allowlist == ()
    with pytest.raises(FrozenInstanceError):
        snapshot.catalog_hash = "changed"  # type: ignore[misc]


def test_runtime_bundle_caches_a_snapshot_and_does_not_expose_tools() -> None:
    bundle = _bundle()

    first = bundle.snapshot
    second = bundle.snapshot

    assert first is second
    assert not hasattr(first, "tools")


def test_snapshot_captures_skill_allowlist_without_skill_body() -> None:
    bundle = CodingToolBundle(
        tools=(),
        deferred_setup=DeferredToolSetup(),
        capability_revision="cap-r1",
        capability_ids_by_tool_name={"read_file": "local:read_file"},
        capability_count=1,
        active_skill_allowed_tools=frozenset({"read_file", "tool_search"}),
    )

    snapshot = bundle.snapshot

    assert snapshot.skill_scope_active is True
    assert snapshot.skill_allowlist == ("read_file", "tool_search")
    assert "prompt" not in snapshot.as_dict()
