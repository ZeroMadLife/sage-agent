"""Deferred tool discovery and fail-closed schema promotion.

ToolNode receives every executable tool, while the model sees deferred schemas
only after ``tool_search`` records a promotion in checkpoint state. Catalog
hashes bind persisted names to the exact schema revision that authorized them.
"""

from __future__ import annotations

import hashlib
import html
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Annotated, Any, override

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langchain_core.utils.function_calling import convert_to_openai_function
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from sage_harness.capabilities import (
    CapabilityBinding,
    CapabilityMatch,
    CapabilityRegistry,
    CapabilitySelectionIndex,
    CapabilitySelectionOutcome,
    CapabilitySurface,
)
from sage_harness.config import HarnessRunContext
from sage_harness.state import SageThreadState

MAX_SEARCH_RESULTS = 5
MAX_QUERY_CHARS = 200


@dataclass(frozen=True)
class DeferredToolCatalog:
    """Immutable, bounded catalog of model-discoverable tools."""

    tools: tuple[BaseTool, ...]
    selection_index: CapabilitySelectionIndex

    def __post_init__(self) -> None:
        names = [tool.name for tool in self.tools]
        if not names:
            raise ValueError("Deferred tool catalog must not be empty")
        if any(not name.strip() for name in names):
            raise ValueError("Deferred tools require non-empty names")
        if "tool_search" in names:
            raise ValueError("Deferred tool catalog cannot contain tool_search")
        if len(names) != len(set(names)):
            raise ValueError("Deferred tool names must be unique")
        binding_names = {binding.tool_name for binding in self.selection_index.bindings}
        if binding_names != set(names):
            raise ValueError("Every deferred tool requires exactly one capability binding")

    @cached_property
    def names(self) -> frozenset[str]:
        return frozenset(tool.name for tool in self.tools)

    @cached_property
    def hash(self) -> str:
        canonical = {
            "registry_revision": self.selection_index.revision,
            "bindings": [
                {
                    "capability_id": binding.capability_id,
                    "tool_name": binding.tool_name,
                }
                for binding in self.selection_index.bindings
            ],
            "allowed_tool_names": (
                sorted(self.selection_index.allowed_tool_names)
                if self.selection_index.allowed_tool_names is not None
                else None
            ),
            "tools": [
                convert_to_openai_function(candidate)
                for candidate in sorted(self.tools, key=lambda item: item.name)
            ],
        }
        payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def search(self, query: str) -> list[BaseTool]:
        """Compatibility helper returning tools only after an exact selection."""
        normalized = query.strip()[:MAX_QUERY_CHARS]
        if not normalized.casefold().startswith("select:"):
            return []
        identifiers = tuple(
            item.strip()
            for item in normalized.split(":", 1)[1].split(",")
            if item.strip()
        )
        outcome = self.select(identifiers)
        by_name = {candidate.name: candidate for candidate in self.tools}
        return [
            by_name[item.tool_name]
            for item in outcome.selected
            if item.tool_name in by_name
        ]

    def discover(self, query: str) -> tuple[CapabilityMatch, ...]:
        return self.selection_index.discover(query)

    def select(self, identifiers: tuple[str, ...]) -> CapabilitySelectionOutcome:
        return self.selection_index.select(identifiers)


@dataclass(frozen=True, slots=True)
class DeferredToolSetup:
    """Catalog-scoped state needed by the graph and filtering middleware."""

    tool_search: BaseTool | None = None
    deferred_names: frozenset[str] = frozenset()
    catalog_hash: str | None = None
    selection_index: CapabilitySelectionIndex | None = None

    @property
    def enabled(self) -> bool:
        return self.tool_search is not None


def build_tool_search_tool(catalog: DeferredToolCatalog) -> BaseTool:
    """Create a state-updating discovery tool for one immutable catalog."""
    catalog_hash = catalog.hash

    @tool
    def tool_search(
        query: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ) -> Command[Any]:
        """Find deferred tools and authorize their exact schemas for this thread.

        First use a short keyword query to inspect bounded public metadata. Then use
        ``select:<capability_id>`` to authorize an exact schema for the next model turn.
        """
        normalized = str(query).strip()[:MAX_QUERY_CHARS]
        names: list[str] = []
        capability_ids: list[str] = []
        if normalized.casefold().startswith("select:"):
            requested = tuple(
                item.strip()
                for item in normalized.split(":", 1)[1].split(",")
                if item.strip()
            )
            outcome = catalog.select(requested)
            by_name = {candidate.name: candidate for candidate in catalog.tools}
            selected_payload: list[dict[str, object]] = []
            for match in outcome.selected:
                item = match.as_dict()
                if match.tool_name is not None and match.tool_name in by_name:
                    item["schema"] = convert_to_openai_function(by_name[match.tool_name])
                    names.append(match.tool_name)
                    capability_ids.append(match.descriptor.capability_id)
                selected_payload.append(item)
            content = json.dumps(
                {
                    "status": "selected" if selected_payload else "rejected",
                    "catalog_revision": catalog.selection_index.revision,
                    "selected": selected_payload,
                    "rejected": [item.as_dict() for item in outcome.rejected],
                },
                ensure_ascii=False,
                indent=2,
            )
            writer = _stream_writer()
            if writer is not None:
                if capability_ids:
                    writer(
                        {
                            "type": "capability_selected",
                            "version": 1,
                            "catalog_revision": catalog.selection_index.revision,
                            "catalog_hash": catalog_hash,
                            "capability_ids": capability_ids,
                            "selected_count": len(capability_ids),
                        }
                    )
                if outcome.rejected:
                    writer(
                        {
                            "type": "capability_selection_failed",
                            "version": 1,
                            "catalog_revision": catalog.selection_index.revision,
                            "catalog_hash": catalog_hash,
                            "failure_categories": sorted(
                                {item.code for item in outcome.rejected}
                            ),
                            "rejected_count": len(outcome.rejected),
                        }
                    )
        else:
            matched = catalog.discover(normalized)
            available = [
                item for item in matched if item.descriptor.availability == "available"
            ]
            if available:
                instruction = (
                    "Call tool_search again with select:<capability_id> to promote one "
                    "exact schema. Select only results whose availability is available."
                )
            elif matched:
                instruction = (
                    "No executable capability is currently available in these results. "
                    "Do not select or retry an unavailable capability."
                )
            else:
                instruction = "Choose another bounded search query."
            content = json.dumps(
                {
                    "status": "matches" if matched else "no_match",
                    "catalog_revision": catalog.selection_index.revision,
                    "query": normalized,
                    "results": [item.as_dict() for item in matched],
                    "instruction": instruction,
                },
                ensure_ascii=False,
                indent=2,
            )
        return Command(
            update={
                "promoted_tools": {
                    "catalog_hash": catalog_hash,
                    "names": names,
                    "capability_ids": capability_ids,
                },
                "messages": [
                    ToolMessage(
                        content=content,
                        tool_call_id=tool_call_id,
                        name="tool_search",
                    )
                ],
            }
        )

    return tool_search


def _stream_writer() -> Callable[[Any], None] | None:
    try:
        from langgraph.config import get_stream_writer

        return get_stream_writer()
    except (KeyError, RuntimeError):
        return None


def _capability_binding(
    registry: CapabilityRegistry,
    candidate: BaseTool,
) -> CapabilityBinding:
    metadata = candidate.metadata if isinstance(candidate.metadata, Mapping) else {}
    configured_id = str(metadata.get("capability_id", "")).strip()
    if configured_id:
        descriptor = registry.get(configured_id)
        matches = [descriptor] if descriptor is not None else []
    else:
        matches = [
            descriptor
            for descriptor in registry.list()
            if descriptor.kind == "tool"
            and descriptor.deferred
            and descriptor.name == candidate.name
        ]
    if len(matches) != 1 or matches[0] is None:
        raise ValueError(
            f"Deferred tool '{candidate.name}' requires one stable capability descriptor"
        )
    return CapabilityBinding(matches[0].capability_id, candidate.name)


def assemble_deferred_tools(
    resident_tools: Sequence[BaseTool],
    deferred_tools: Sequence[BaseTool],
    *,
    enabled: bool,
    capability_registry: CapabilityRegistry,
    surface: CapabilitySurface = "coding",
    allowed_tool_names: frozenset[str] | None = None,
) -> tuple[list[BaseTool], DeferredToolSetup]:
    """Assemble executable tools and the catalog that controls model visibility."""
    residents = list(resident_tools)
    deferred = list(deferred_tools)
    names = [candidate.name for candidate in [*residents, *deferred]]
    if len(names) != len(set(names)):
        raise ValueError("Resident and deferred tool names must be unique")
    if not enabled or not deferred:
        return [*residents, *deferred], DeferredToolSetup()

    bindings = tuple(
        _capability_binding(capability_registry, candidate) for candidate in deferred
    )
    selection_index = CapabilitySelectionIndex(
        capability_registry,
        bindings=bindings,
        surface=surface,
        allowed_tool_names=allowed_tool_names,
    )
    catalog = DeferredToolCatalog(tuple(deferred), selection_index)
    search_tool = build_tool_search_tool(catalog)
    if search_tool.name in names:
        raise ValueError("tool_search conflicts with an existing tool")
    return (
        [*residents, *deferred, search_tool],
        DeferredToolSetup(
            tool_search=search_tool,
            deferred_names=catalog.names,
            catalog_hash=catalog.hash,
            selection_index=selection_index,
        ),
    )


def render_deferred_tool_index(setup: DeferredToolSetup) -> str:
    """Expose only escaped names; schemas remain behind ``tool_search``."""
    if not setup.enabled:
        return ""
    if setup.selection_index is None:
        raise ValueError("Enabled deferred tool setup requires a selection index")
    entries = "\n".join(
        "\t".join(
            (
                html.escape(match.descriptor.capability_id, quote=False),
                html.escape(match.descriptor.name, quote=False),
                match.descriptor.origin,
                match.descriptor.kind,
            )
        )
        for match in setup.selection_index.list_discoverable()
    )
    return (
        "<available-capabilities>\n"
        f"{entries}\n"
        "Use tool_search with a keyword, then select one stable capability_id before calling "
        "a deferred tool.\n"
        "</available-capabilities>"
    )


class DeferredToolFilterMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Hide unpromoted schemas and reject forged deferred tool calls."""

    state_schema = SageThreadState

    def __init__(
        self,
        capability_bindings: tuple[CapabilityBinding, ...],
        catalog_hash: str,
    ) -> None:
        super().__init__()
        if not capability_bindings or not catalog_hash.strip():
            raise ValueError("Deferred tool filtering requires bindings and a catalog hash")
        self._tool_names_by_capability_id = {
            binding.capability_id: binding.tool_name for binding in capability_bindings
        }
        self._deferred_names = frozenset(self._tool_names_by_capability_id.values())
        self._catalog_hash = catalog_hash

    def _promoted(self, state: object) -> set[str]:
        if not isinstance(state, Mapping):
            return set()
        promoted = state.get("promoted_tools")
        if not isinstance(promoted, Mapping):
            return set()
        if promoted.get("catalog_hash") != self._catalog_hash:
            return set()
        capability_ids = promoted.get("capability_ids")
        if not isinstance(capability_ids, list | tuple | set | frozenset):
            return set()
        return {
            self._tool_names_by_capability_id[str(capability_id)]
            for capability_id in capability_ids
            if str(capability_id) in self._tool_names_by_capability_id
        }

    def _hidden(self, state: object) -> set[str]:
        return set(self._deferred_names) - self._promoted(state)

    def _filter_request(
        self,
        request: ModelRequest[HarnessRunContext],
    ) -> ModelRequest[HarnessRunContext]:
        hidden = self._hidden(request.state)
        if not hidden or not request.tools:
            return request
        return request.override(
            tools=[
                candidate
                for candidate in request.tools
                if _model_tool_name(candidate) not in hidden
            ]
        )

    def _blocked_message(self, request: ToolCallRequest) -> ToolMessage | None:
        name = str(request.tool_call.get("name") or "")
        if not name or name not in self._hidden(request.state):
            return None
        return ToolMessage(
            content=(
                f"Tool '{name}' is deferred and has not been promoted. "
                "Call tool_search first, then retry with the returned schema."
            ),
            tool_call_id=str(request.tool_call.get("id") or "missing_tool_call_id"),
            name=name,
            status="error",
        )

    @override
    def wrap_model_call(
        self,
        request: ModelRequest[HarnessRunContext],
        handler: Callable[[ModelRequest[HarnessRunContext]], ModelResponse],
    ) -> ModelCallResult:
        return handler(self._filter_request(request))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[HarnessRunContext],
        handler: Callable[[ModelRequest[HarnessRunContext]], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        return await handler(self._filter_request(request))

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._blocked_message(request)
        return blocked if blocked is not None else handler(request)

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        blocked = self._blocked_message(request)
        return blocked if blocked is not None else await handler(request)


def _model_tool_name(candidate: BaseTool | dict[str, Any]) -> str:
    if isinstance(candidate, BaseTool):
        return candidate.name
    direct = candidate.get("name")
    if isinstance(direct, str):
        return direct
    function = candidate.get("function")
    if isinstance(function, Mapping):
        return str(function.get("name") or "")
    return ""


__all__ = [
    "DeferredToolCatalog",
    "DeferredToolFilterMiddleware",
    "DeferredToolSetup",
    "assemble_deferred_tools",
    "build_tool_search_tool",
    "render_deferred_tool_index",
]
