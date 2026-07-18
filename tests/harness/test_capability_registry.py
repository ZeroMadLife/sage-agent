"""Neutral Capability Registry contract tests."""

from __future__ import annotations

import pytest
from sage_harness import CapabilityDescriptor, CapabilityRegistry


def _descriptor(
    capability_id: str,
    *,
    name: str = "search",
    origin: str = "local",
    surfaces: tuple[str, ...] = ("coding",),
    availability: str = "available",
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=capability_id,
        name=name,
        origin=origin,
        kind="tool",
        revision="r1",
        description="Search a bounded source",
        surfaces=surfaces,
        risk="low",
        permission="none",
        deferred=False,
        remote_content=False,
        availability=availability,
        timeout_seconds=30.0,
        tags=("search",),
    )


def test_registry_keeps_same_display_name_from_different_origins() -> None:
    registry = CapabilityRegistry(
        [
            _descriptor("local:search"),
            _descriptor("mcp:github:search", origin="mcp"),
        ]
    )

    assert [item.capability_id for item in registry.list()] == [
        "local:search",
        "mcp:github:search",
    ]


def test_registry_rejects_conflicting_duplicate_stable_id() -> None:
    registry = CapabilityRegistry([_descriptor("local:search")])

    with pytest.raises(ValueError, match="conflicting capability id"):
        registry.register(_descriptor("local:search", name="other"))


def test_registry_query_filters_surface_origin_availability_and_text() -> None:
    registry = CapabilityRegistry(
        [
            _descriptor("local:search", surfaces=("coding", "knowledge")),
            _descriptor(
                "skill:project:review",
                name="review",
                origin="skill",
                surfaces=("coding",),
            ),
            _descriptor(
                "mcp:github:search",
                origin="mcp",
                surfaces=("knowledge",),
                availability="unavailable",
            ),
        ]
    )

    assert [item.capability_id for item in registry.query(surface="knowledge")] == [
        "local:search",
        "mcp:github:search",
    ]
    assert [
        item.capability_id for item in registry.query(origins=frozenset({"skill"}), text="review")
    ] == ["skill:project:review"]
    assert (
        registry.query(
            origins=frozenset({"mcp"}),
            availability=frozenset({"available"}),
        )
        == ()
    )


def test_registry_revision_is_order_independent_and_changes_with_public_metadata() -> None:
    first = _descriptor("local:search")
    second = _descriptor("skill:project:review", name="review", origin="skill")

    assert (
        CapabilityRegistry([first, second]).revision == CapabilityRegistry([second, first]).revision
    )
    assert (
        CapabilityRegistry([first]).revision
        != CapabilityRegistry([_descriptor("local:search", surfaces=("coding", "growth"))]).revision
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capability_id", "/Users/example/private"),
        ("revision", ""),
        ("description", "line one\x00line two"),
    ],
)
def test_descriptor_rejects_unbounded_or_unsafe_public_fields(field: str, value: str) -> None:
    values = _descriptor("local:search").as_dict()
    values[field] = value

    with pytest.raises(ValueError):
        CapabilityDescriptor(**values)
