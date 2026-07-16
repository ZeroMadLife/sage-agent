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

from sage_harness.config import HarnessRunContext
from sage_harness.state import SageThreadState

MAX_SEARCH_RESULTS = 5
MAX_QUERY_CHARS = 200


@dataclass(frozen=True)
class DeferredToolCatalog:
    """Immutable, bounded catalog of model-discoverable tools."""

    tools: tuple[BaseTool, ...]

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

    @cached_property
    def names(self) -> frozenset[str]:
        return frozenset(tool.name for tool in self.tools)

    @cached_property
    def hash(self) -> str:
        canonical = [
            convert_to_openai_function(candidate)
            for candidate in sorted(self.tools, key=lambda item: item.name)
        ]
        payload = json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def search(self, query: str) -> list[BaseTool]:
        """Return at most five deterministic matches without evaluating regex."""
        normalized = query.strip()[:MAX_QUERY_CHARS]
        if not normalized:
            return []

        if normalized.casefold().startswith("select:"):
            requested = [
                name.strip()
                for name in normalized.split(":", 1)[1].split(",")
                if name.strip()
            ]
            by_name = {candidate.name: candidate for candidate in self.tools}
            return [by_name[name] for name in requested if name in by_name][
                :MAX_SEARCH_RESULTS
            ]

        query_text = normalized.casefold()
        tokens = [token for token in query_text.split() if token]
        ranked: list[tuple[int, str, BaseTool]] = []
        for candidate in self.tools:
            metadata = candidate.metadata if isinstance(candidate.metadata, Mapping) else {}
            category = str(metadata.get("category", ""))
            name = candidate.name.casefold()
            description = str(candidate.description or "").casefold()
            searchable = f"{name} {description} {category.casefold()}"
            if query_text not in searchable and not any(token in searchable for token in tokens):
                continue
            score = 4 if query_text == name else 3 if query_text in name else 1
            score += sum(1 for token in tokens if token in searchable)
            ranked.append((score, candidate.name, candidate))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [candidate for _, _, candidate in ranked[:MAX_SEARCH_RESULTS]]


@dataclass(frozen=True, slots=True)
class DeferredToolSetup:
    """Catalog-scoped state needed by the graph and filtering middleware."""

    tool_search: BaseTool | None = None
    deferred_names: frozenset[str] = frozenset()
    catalog_hash: str | None = None

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

        Use ``select:name_a,name_b`` for exact names, or a short keyword query.
        A successful result makes the returned tools callable on the next model turn.
        """
        matched = catalog.search(query)
        names = [candidate.name for candidate in matched]
        if matched:
            content = json.dumps(
                [convert_to_openai_function(candidate) for candidate in matched],
                ensure_ascii=False,
                indent=2,
            )
        else:
            content = json.dumps(
                {"status": "no_match", "query": query[:MAX_QUERY_CHARS]},
                ensure_ascii=False,
            )
        return Command(
            update={
                "promoted_tools": {"catalog_hash": catalog_hash, "names": names},
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


def assemble_deferred_tools(
    resident_tools: Sequence[BaseTool],
    deferred_tools: Sequence[BaseTool],
    *,
    enabled: bool,
) -> tuple[list[BaseTool], DeferredToolSetup]:
    """Assemble executable tools and the catalog that controls model visibility."""
    residents = list(resident_tools)
    deferred = list(deferred_tools)
    names = [candidate.name for candidate in [*residents, *deferred]]
    if len(names) != len(set(names)):
        raise ValueError("Resident and deferred tool names must be unique")
    if not enabled or not deferred:
        return [*residents, *deferred], DeferredToolSetup()

    catalog = DeferredToolCatalog(tuple(deferred))
    search_tool = build_tool_search_tool(catalog)
    if search_tool.name in names:
        raise ValueError("tool_search conflicts with an existing tool")
    return (
        [*residents, *deferred, search_tool],
        DeferredToolSetup(
            tool_search=search_tool,
            deferred_names=catalog.names,
            catalog_hash=catalog.hash,
        ),
    )


def render_deferred_tool_index(setup: DeferredToolSetup) -> str:
    """Expose only escaped names; schemas remain behind ``tool_search``."""
    if not setup.enabled:
        return ""
    names = "\n".join(html.escape(name, quote=False) for name in sorted(setup.deferred_names))
    return (
        "<available-deferred-tools>\n"
        f"{names}\n"
        "Use tool_search before calling any tool listed above.\n"
        "</available-deferred-tools>"
    )


class DeferredToolFilterMiddleware(AgentMiddleware[SageThreadState, HarnessRunContext]):
    """Hide unpromoted schemas and reject forged deferred tool calls."""

    state_schema = SageThreadState

    def __init__(self, deferred_names: frozenset[str], catalog_hash: str) -> None:
        super().__init__()
        if not deferred_names or not catalog_hash.strip():
            raise ValueError("Deferred tool filtering requires names and a catalog hash")
        self._deferred_names = deferred_names
        self._catalog_hash = catalog_hash

    def _promoted(self, state: object) -> set[str]:
        if not isinstance(state, Mapping):
            return set()
        promoted = state.get("promoted_tools")
        if not isinstance(promoted, Mapping):
            return set()
        if promoted.get("catalog_hash") != self._catalog_hash:
            return set()
        names = promoted.get("names")
        if not isinstance(names, list | tuple | set | frozenset):
            return set()
        return {str(name) for name in names} & set(self._deferred_names)

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
