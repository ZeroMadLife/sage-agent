"""Deferred catalog and model-visibility policy tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt.tool_node import ToolCallRequest
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


def test_catalog_search_is_bounded_deterministic_and_revision_hashed() -> None:
    catalog = DeferredToolCatalog((deferred_tool,))

    assert [item.name for item in catalog.search("deferred")] == ["deferred_tool"]
    assert [item.name for item in catalog.search("select:deferred_tool,missing")] == [
        "deferred_tool"
    ]
    assert catalog.search("") == []
    assert len(catalog.hash) == 16

    with pytest.raises(ValueError, match="unique"):
        DeferredToolCatalog((deferred_tool, deferred_tool))


def test_assembly_exposes_names_but_not_schemas_before_promotion() -> None:
    tools, setup = assemble_deferred_tools(
        [resident_tool],
        [deferred_tool],
        enabled=True,
    )

    assert {item.name for item in tools} == {
        "resident_tool",
        "deferred_tool",
        "tool_search",
    }
    assert setup.deferred_names == frozenset({"deferred_tool"})
    prompt = render_deferred_tool_index(setup)
    assert "deferred_tool" in prompt
    assert "properties" not in prompt


def test_middleware_filters_stale_catalog_and_blocks_forged_call() -> None:
    middleware = DeferredToolFilterMiddleware(
        frozenset({"deferred_tool"}),
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


def test_middleware_exposes_only_current_catalog_promotions() -> None:
    middleware = DeferredToolFilterMiddleware(
        frozenset({"deferred_tool"}),
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
