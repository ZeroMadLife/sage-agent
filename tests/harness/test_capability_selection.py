"""Stable capability discovery and selection contract tests."""

from __future__ import annotations

import pytest
from sage_harness.capabilities import (
    CapabilityBinding,
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilitySelectionIndex,
)


def _descriptor(
    capability_id: str,
    name: str,
    *,
    origin: str = "local",
    kind: str = "tool",
    availability: str = "available",
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=capability_id,
        name=name,
        origin=origin,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        revision=f"rev-{capability_id}",
        description=f"Use {name} for a bounded operation.",
        surfaces=("coding",),
        risk="low",
        permission="none",
        deferred=True,
        remote_content=False,
        availability=availability,  # type: ignore[arg-type]
        timeout_seconds=30.0,
        tags=(origin,),
    )


def test_discovery_preserves_same_display_names_without_exposing_schema() -> None:
    registry = CapabilityRegistry(
        (
            _descriptor("local:review", "review"),
            _descriptor("skill:builtin:review", "review", origin="skill", kind="workflow"),
        )
    )
    index = CapabilitySelectionIndex(
        registry,
        bindings=(CapabilityBinding("local:review", "review"),),
        surface="coding",
    )

    results = index.discover("review")

    assert [item.descriptor.capability_id for item in results] == [
        "local:review",
        "skill:builtin:review",
    ]
    payload = [item.as_dict() for item in results]
    assert payload[0]["promotable"] is True
    assert payload[1]["invocation"] == {
        "type": "user_command",
        "command": "/review",
        "requires_user_action": True,
    }
    assert "schema" not in str(payload).lower()


def test_exact_stable_id_selects_one_bound_tool_and_legacy_name_must_be_unique() -> None:
    registry = CapabilityRegistry(
        (
            _descriptor("local:lookup", "lookup"),
            _descriptor("mcp:scenic:lookup", "lookup", origin="mcp"),
        )
    )
    index = CapabilitySelectionIndex(
        registry,
        bindings=(
            CapabilityBinding("local:lookup", "local_lookup"),
            CapabilityBinding("mcp:scenic:lookup", "scenic_lookup"),
        ),
        surface="coding",
    )

    exact = index.select(("mcp:scenic:lookup",))
    ambiguous = index.select(("lookup",))

    assert [item.descriptor.capability_id for item in exact.selected] == [
        "mcp:scenic:lookup"
    ]
    assert exact.selected[0].tool_name == "scenic_lookup"
    assert ambiguous.selected == ()
    assert ambiguous.rejected[0].code == "ambiguous"
    assert ambiguous.rejected[0].candidate_ids == (
        "local:lookup",
        "mcp:scenic:lookup",
    )


def test_unavailable_and_skill_disallowed_capabilities_fail_closed() -> None:
    registry = CapabilityRegistry(
        (
            _descriptor("local:todo_list", "todo_list"),
            _descriptor(
                "mcp:scenic:stale",
                "scenic_stale",
                origin="mcp",
                availability="stale",
            ),
        )
    )
    index = CapabilitySelectionIndex(
        registry,
        bindings=(
            CapabilityBinding("local:todo_list", "todo_list"),
            CapabilityBinding("mcp:scenic:stale", "scenic_stale"),
        ),
        surface="coding",
        allowed_tool_names=frozenset({"read_file"}),
    )

    assert index.discover("todo") == ()
    disallowed = index.select(("local:todo_list",))
    stale = index.select(("mcp:scenic:stale",))

    assert disallowed.rejected[0].code == "disallowed"
    assert stale.rejected[0].code == "unavailable"


def test_bindings_reject_non_tool_and_unknown_capabilities() -> None:
    workflow = _descriptor(
        "skill:builtin:review",
        "review",
        origin="skill",
        kind="workflow",
    )
    registry = CapabilityRegistry((workflow,))

    with pytest.raises(ValueError, match="unknown capability"):
        CapabilitySelectionIndex(
            registry,
            bindings=(CapabilityBinding("local:missing", "missing"),),
            surface="coding",
        )
    with pytest.raises(ValueError, match="deferred tool capability"):
        CapabilitySelectionIndex(
            registry,
            bindings=(CapabilityBinding("skill:builtin:review", "review"),),
            surface="coding",
        )
