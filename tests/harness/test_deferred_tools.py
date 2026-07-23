"""Deferred catalog and model-visibility policy tests."""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import MagicMock

import pytest
import sage_harness.deferred_tools as deferred_tools_module
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolCallRequest
from sage_harness.capabilities import (
    CapabilityBinding,
    CapabilityDescriptor,
    CapabilityRegistry,
    CapabilitySelectionIndex,
)
from sage_harness.deferred_tools import (
    DeferredToolCatalog,
    DeferredToolFilterMiddleware,
    assemble_deferred_tools,
    render_deferred_tool_index,
)


@tool
def resident_tool(value: str) -> str:
    """Run a resident operation."""
    return value


@tool
def deferred_tool(value: str) -> str:
    """Run a deferred operation."""
    return value


def _descriptor(
    capability_id: str,
    name: str,
    *,
    origin: str = "local",
    kind: str = "tool",
) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        capability_id=capability_id,
        name=name,
        origin=origin,  # type: ignore[arg-type]
        kind=kind,  # type: ignore[arg-type]
        revision=f"rev-{capability_id}",
        description=f"Use {name} for a deferred operation.",
        surfaces=("coding",),
        risk="low",
        permission="none",
        deferred=True,
        remote_content=False,
        availability="available",
        timeout_seconds=30.0,
        tags=(origin,),
    )


def _registry(*extra: CapabilityDescriptor) -> CapabilityRegistry:
    return CapabilityRegistry((_descriptor("local:deferred_tool", "deferred_tool"), *extra))


def _catalog() -> DeferredToolCatalog:
    registry = _registry()
    return DeferredToolCatalog(
        (deferred_tool,),
        CapabilitySelectionIndex(
            registry,
            bindings=(CapabilityBinding("local:deferred_tool", "deferred_tool"),),
            surface="coding",
        ),
    )


def test_catalog_search_is_bounded_deterministic_and_revision_hashed() -> None:
    catalog = _catalog()

    assert [item.descriptor.capability_id for item in catalog.discover("deferred")] == [
        "local:deferred_tool"
    ]
    assert catalog.search("deferred") == []
    assert [item.name for item in catalog.search("select:deferred_tool,missing")] == [
        "deferred_tool"
    ]
    assert catalog.search("") == []
    assert len(catalog.hash) == 16

    with pytest.raises(ValueError, match="unique"):
        DeferredToolCatalog(
            (deferred_tool, deferred_tool),
            catalog.selection_index,
        )


def test_assembly_exposes_names_but_not_schemas_before_promotion() -> None:
    tools, setup = assemble_deferred_tools(
        [resident_tool],
        [deferred_tool],
        enabled=True,
        capability_registry=_registry(
            _descriptor("skill:builtin:deferred", "deferred_tool", origin="skill", kind="workflow")
        ),
    )

    assert {item.name for item in tools} == {
        "resident_tool",
        "deferred_tool",
        "tool_search",
    }
    assert setup.deferred_names == frozenset({"deferred_tool"})
    prompt = render_deferred_tool_index(setup)
    assert "local:deferred_tool" in prompt
    assert "skill:builtin:deferred" in prompt
    assert "properties" not in prompt


def test_tool_search_returns_metadata_before_stable_id_schema_promotion() -> None:
    catalog = _catalog()
    search_tool = assemble_deferred_tools(
        [resident_tool],
        [deferred_tool],
        enabled=True,
        capability_registry=_registry(),
    )[1].tool_search
    assert search_tool is not None

    discovered = search_tool.func(query="deferred", tool_call_id="call-discover")
    selected = search_tool.func(
        query="select:local:deferred_tool",
        tool_call_id="call-select",
    )
    discovered_payload = json.loads(str(discovered.update["messages"][0].content))
    selected_payload = json.loads(str(selected.update["messages"][0].content))

    assert discovered_payload["status"] == "matches"
    assert "schema" not in discovered_payload["results"][0]
    assert discovered.update["promoted_tools"] == {
        "catalog_hash": catalog.hash,
        "names": [],
        "capability_ids": [],
    }
    assert selected_payload["status"] == "selected"
    assert selected_payload["selected"][0]["capability_id"] == "local:deferred_tool"
    assert selected_payload["selected"][0]["schema"]["name"] == "deferred_tool"
    assert selected.update["promoted_tools"] == {
        "catalog_hash": catalog.hash,
        "names": ["deferred_tool"],
        "capability_ids": ["local:deferred_tool"],
    }


def test_tool_search_tells_model_not_to_select_unavailable_capability() -> None:
    unavailable = replace(
        _descriptor("subagent:research", "Research", origin="subagent", kind="delegate"),
        availability="unavailable",
    )
    registry = _registry(unavailable)
    search_tool = assemble_deferred_tools(
        [resident_tool],
        [deferred_tool],
        enabled=True,
        capability_registry=registry,
    )[1].tool_search
    assert search_tool is not None

    result = search_tool.func(query="research", tool_call_id="call-research")
    payload = json.loads(str(result.update["messages"][0].content))

    assert payload["status"] == "matches"
    assert payload["results"][0]["availability"] == "unavailable"
    assert "Do not select or retry" in payload["instruction"]


def test_tool_search_emits_only_safe_selection_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict[str, object]] = []
    monkeypatch.setattr(deferred_tools_module, "_stream_writer", lambda: events.append)
    search_tool = assemble_deferred_tools(
        [resident_tool],
        [deferred_tool],
        enabled=True,
        capability_registry=_registry(),
    )[1].tool_search
    assert search_tool is not None

    search_tool.func(
        query="select:local:deferred_tool,api_key=secret,/Users/example/private",
        tool_call_id="call-select",
    )

    assert events[0]["type"] == "capability_selected"
    assert events[0]["capability_ids"] == ["local:deferred_tool"]
    assert events[1]["type"] == "capability_selection_failed"
    assert events[1]["failure_categories"] == ["unknown"]
    assert "secret" not in str(events)
    assert "/Users/" not in str(events)
    assert "schema" not in str(events)


def test_skill_allowlist_hides_disallowed_discovery_and_changes_catalog_hash() -> None:
    unrestricted = assemble_deferred_tools(
        [resident_tool],
        [deferred_tool],
        enabled=True,
        capability_registry=_registry(),
    )[1]
    restricted = assemble_deferred_tools(
        [resident_tool],
        [deferred_tool],
        enabled=True,
        capability_registry=_registry(),
        allowed_tool_names=frozenset({"read_file"}),
    )[1]
    assert unrestricted.tool_search is not None
    assert restricted.tool_search is not None

    discovered = restricted.tool_search.func(
        query="deferred",
        tool_call_id="call-discover",
    )
    selected = restricted.tool_search.func(
        query="select:local:deferred_tool",
        tool_call_id="call-select",
    )
    discovered_payload = json.loads(str(discovered.update["messages"][0].content))
    selected_payload = json.loads(str(selected.update["messages"][0].content))

    assert discovered_payload["status"] == "no_match"
    assert selected_payload["status"] == "rejected"
    assert selected_payload["rejected"][0]["code"] == "disallowed"
    assert restricted.catalog_hash != unrestricted.catalog_hash


def test_middleware_filters_stale_catalog_and_blocks_forged_call() -> None:
    middleware = DeferredToolFilterMiddleware(
        (CapabilityBinding("local:deferred_tool", "deferred_tool"),),
        "catalog-current",
    )
    captured: list[ModelRequest] = []

    def capture(request: ModelRequest) -> ModelResponse:
        captured.append(request)
        return ModelResponse(result=[AIMessage(content="ok")])

    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
        messages=[],
        tools=[resident_tool, deferred_tool],
        state={
            "promoted_tools": {
                "catalog_hash": "catalog-stale",
                "names": ["deferred_tool"],
                "capability_ids": ["local:deferred_tool"],
            }
        },
    )
    middleware.wrap_model_call(request, capture)

    assert [item.name for item in captured[0].tools or []] == ["resident_tool"]

    tool_request = ToolCallRequest(
        tool_call={
            "name": "deferred_tool",
            "args": {"value": "x"},
            "id": "call-forged",
            "type": "tool_call",
        },
        tool=deferred_tool,
        state={},
        runtime=MagicMock(),
    )
    result = middleware.wrap_tool_call(
        tool_request,
        lambda _: ToolMessage(
            content="executed",
            tool_call_id="call-forged",
            name="deferred_tool",
        ),
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "tool_search" in str(result.content)


def test_middleware_rejects_legacy_names_without_stable_capability_ids() -> None:
    middleware = DeferredToolFilterMiddleware(
        (CapabilityBinding("local:deferred_tool", "deferred_tool"),),
        "catalog-current",
    )
    captured: list[ModelRequest] = []
    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
        messages=[],
        tools=[resident_tool, deferred_tool],
        state={
            "promoted_tools": {
                "catalog_hash": "catalog-current",
                "names": ["deferred_tool"],
            }
        },
    )

    middleware.wrap_model_call(
        request,
        lambda filtered: (
            captured.append(filtered) or ModelResponse(result=[AIMessage(content="ok")])
        ),
    )

    assert [item.name for item in captured[0].tools or []] == ["resident_tool"]


def test_middleware_exposes_only_current_catalog_promotions() -> None:
    middleware = DeferredToolFilterMiddleware(
        (CapabilityBinding("local:deferred_tool", "deferred_tool"),),
        "catalog-current",
    )
    captured: list[ModelRequest] = []
    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[AIMessage(content="unused")]),
        messages=[],
        tools=[resident_tool, deferred_tool],
        state={
            "promoted_tools": {
                "catalog_hash": "catalog-current",
                "names": ["deferred_tool", "forged_tool"],
                "capability_ids": ["local:deferred_tool", "local:forged_tool"],
            }
        },
    )

    middleware.wrap_model_call(
        request,
        lambda filtered: (
            captured.append(filtered) or ModelResponse(result=[AIMessage(content="ok")])
        ),
    )

    assert [item.name for item in captured[0].tools or []] == [
        "resident_tool",
        "deferred_tool",
    ]
