"""Scoped MCP discovery and deferred tool preparation.

The manager deliberately owns only sanitized configuration references and
transport handles supplied by the application. Connection secrets stay inside
the transport adapter and never enter a checkpoint, prompt, or public catalog.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from langchain_core.tools import BaseTool, StructuredTool

from sage_harness.ports import McpServerReference

McpTransport = Literal["stdio", "sse", "streamable_http"]
McpServerStatus = Literal[
    "configured",
    "unconfigured",
    "connecting",
    "connected",
    "degraded",
    "error",
    "stale",
    "closed",
]
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MAX_DESCRIPTION_CHARS = 4000
_MAX_TOOL_SCHEMA_CHARS = 64_000


@dataclass(frozen=True, slots=True)
class McpScope:
    """Owner and workspace boundary for one MCP catalog and its sessions."""

    owner_id: str
    workspace_id: str
    thread_id: str

    def __post_init__(self) -> None:
        for label, value in (
            ("owner_id", self.owner_id),
            ("workspace_id", self.workspace_id),
            ("thread_id", self.thread_id),
        ):
            if not value.strip() or len(value) > 256:
                raise ValueError(f"MCP scope {label} must be non-empty and bounded")

    @property
    def key(self) -> tuple[str, str, str]:
        return self.owner_id, self.workspace_id, self.thread_id


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """Sanitized server reference; raw connection settings stay in the adapter."""

    name: str
    transport: McpTransport
    status: McpServerStatus = "configured"
    remote_content: bool = True
    capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.fullmatch(self.name):
            raise ValueError("MCP server names must use [A-Za-z0-9_-]")


@dataclass(frozen=True, slots=True)
class McpConfigSnapshot:
    """Immutable, revisioned MCP configuration without credentials."""

    revision: str
    servers: tuple[McpServerConfig, ...]

    def __post_init__(self) -> None:
        if not self.revision.strip() or len(self.revision) > 128:
            raise ValueError("MCP config revision must be non-empty and bounded")
        names = [server.name for server in self.servers]
        if len(names) != len(set(names)):
            raise ValueError("MCP server names must be unique")


@dataclass(frozen=True, slots=True)
class McpToolDescriptor:
    """Schema and policy metadata for one discovered MCP tool."""

    tool_id: str
    server_name: str
    name: str
    original_name: str
    description: str
    schema_json: str = field(repr=False)
    remote_content: bool = True
    metadata: Mapping[str, object] = field(default_factory=dict, repr=False)
    schema_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.fullmatch(self.server_name) or not _NAME_PATTERN.fullmatch(self.name):
            raise ValueError("MCP tool names must use [A-Za-z0-9_-]")
        if not self.original_name.strip() or len(self.original_name) > 128:
            raise ValueError("MCP original tool name must be non-empty and bounded")
        if len(self.schema_json) > _MAX_TOOL_SCHEMA_CHARS:
            raise ValueError("MCP tool schema exceeds the bounded size")
        try:
            parsed = json.loads(self.schema_json)
        except json.JSONDecodeError as exc:
            raise ValueError("MCP tool schema must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("MCP tool schema must be a JSON object")
        object.__setattr__(
            self,
            "schema_hash",
            hashlib.sha256(self.schema_json.encode("utf-8")).hexdigest()[:16],
        )

    @classmethod
    def from_schema(
        cls,
        *,
        tool_id: str,
        server_name: str,
        name: str,
        original_name: str,
        description: str,
        schema: Mapping[str, object],
        remote_content: bool = True,
        metadata: Mapping[str, object] | None = None,
    ) -> McpToolDescriptor:
        schema_json = json.dumps(schema, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return cls(
            tool_id=tool_id,
            server_name=server_name,
            name=name,
            original_name=original_name,
            description=description[:_MAX_DESCRIPTION_CHARS],
            schema_json=schema_json,
            remote_content=remote_content,
            metadata=dict(metadata or {}),
        )

    def args_schema(self) -> dict[str, object]:
        """Return a fresh schema copy suitable for a LangChain tool wrapper."""
        parsed = json.loads(self.schema_json)
        return parsed if isinstance(parsed, dict) else {}


@dataclass(frozen=True, slots=True)
class McpCatalogSnapshot:
    """Bounded public catalog for one scope and config revision."""

    revision: str
    scope: McpScope
    servers: tuple[McpServerReference, ...]
    tools: tuple[McpToolDescriptor, ...]
    catalog_hash: str


@dataclass(frozen=True, slots=True)
class McpToolSnapshot:
    """Executable wrappers plus the sanitized catalog used to build them."""

    catalog: McpCatalogSnapshot
    tools: tuple[BaseTool, ...]


class McpTransportPort(Protocol):
    """Application-owned discovery/invocation boundary."""

    async def discover(
        self,
        server: McpServerConfig,
        scope: McpScope,
    ) -> Sequence[McpToolDescriptor]: ...

    async def invoke(
        self,
        tool: McpToolDescriptor,
        arguments: Mapping[str, object],
        scope: McpScope,
    ) -> object: ...

    async def close_scope(self, scope: McpScope) -> None: ...

    async def invalidate_revision(self, revision: str) -> None: ...

    async def aclose(self) -> None: ...


class McpManager:
    """Revisioned, scoped MCP catalog with fail-soft per-server discovery."""

    def __init__(
        self,
        snapshot: McpConfigSnapshot,
        transport: McpTransportPort,
        *,
        discovery_timeout_seconds: float = 10.0,
        call_timeout_seconds: float = 30.0,
    ) -> None:
        if discovery_timeout_seconds <= 0 or call_timeout_seconds <= 0:
            raise ValueError("MCP timeouts must be positive")
        self._snapshot = snapshot
        self._transport = transport
        self._discovery_timeout = discovery_timeout_seconds
        self._call_timeout = call_timeout_seconds
        self._cache: dict[tuple[str, tuple[str, str, str]], McpCatalogSnapshot] = {}
        self._locks: dict[tuple[str, tuple[str, str, str]], asyncio.Lock] = {}
        self._closed = False

    @property
    def revision(self) -> str:
        return self._snapshot.revision

    async def list_servers(self) -> tuple[McpServerReference, ...]:
        """Return sanitized current config status without opening connections."""
        return tuple(
            McpServerReference(
                name=server.name,
                transport=server.transport,
                status=server.status,
            )
            for server in self._snapshot.servers
        )

    def cached_catalog(self, scope: McpScope) -> McpCatalogSnapshot | None:
        """Read a current scoped catalog without discovery or network activity."""
        return self._cache.get((self._snapshot.revision, scope.key))

    async def catalog(self, scope: McpScope, *, force: bool = False) -> McpCatalogSnapshot:
        """Discover tools for one isolated scope and config revision."""
        if self._closed:
            raise RuntimeError("MCP manager is closed")
        key = (self._snapshot.revision, scope.key)
        if not force and key in self._cache:
            return self._cache[key]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not force and key in self._cache:
                return self._cache[key]
            servers = list(self._snapshot.servers)
            discovered: list[McpToolDescriptor] = []
            statuses: dict[str, McpServerStatus] = {}

            async def discover_server(
                server: McpServerConfig,
            ) -> tuple[McpServerStatus, tuple[McpToolDescriptor, ...]]:
                if server.status == "unconfigured":
                    return "unconfigured", ()
                try:
                    found = await asyncio.wait_for(
                        self._transport.discover(server, scope),
                        timeout=self._discovery_timeout,
                    )
                    return "connected", self._validate_server_tools(server, found)
                except Exception:
                    return "error", ()

            results = await asyncio.gather(*(discover_server(server) for server in servers))
            for server, (status, tools) in zip(servers, results, strict=True):
                statuses[server.name] = status
                discovered.extend(tools)
            tools_by_server: dict[str, list[str]] = {}
            for tool in discovered:
                tools_by_server.setdefault(tool.server_name, []).append(tool.name)
            updated_servers = tuple(
                McpServerReference(
                    name=server.name,
                    transport=server.transport,
                    status=statuses.get(server.name, server.status),
                    tool_names=tuple(sorted(tools_by_server.get(server.name, []))),
                )
                for server in servers
            )
            catalog_hash = _catalog_hash(self._snapshot.revision, discovered)
            snapshot = McpCatalogSnapshot(
                revision=self._snapshot.revision,
                scope=scope,
                servers=updated_servers,
                tools=tuple(discovered),
                catalog_hash=catalog_hash,
            )
            self._cache[key] = snapshot
            return snapshot

    async def load_tools(self, scope: McpScope, *, force: bool = False) -> McpToolSnapshot:
        """Return deferred LangChain wrappers bound only to a scoped manager."""
        catalog = await self.catalog(scope, force=force)
        wrappers: list[BaseTool] = []
        for descriptor in catalog.tools:
            async def invoke(
                _descriptor: McpToolDescriptor = descriptor,
                **kwargs: object,
            ) -> object:
                return await asyncio.wait_for(
                    self._transport.invoke(_descriptor, kwargs, scope),
                    timeout=self._call_timeout,
                )

            metadata = {
                **dict(descriptor.metadata),
                "category": "mcp",
                "mcp_server": descriptor.server_name,
                "remote_content": descriptor.remote_content,
                "deferred": True,
                "config_revision": catalog.revision,
                "schema_hash": descriptor.schema_hash,
            }
            wrappers.append(
                StructuredTool.from_function(
                    coroutine=invoke,
                    name=descriptor.name,
                    description=descriptor.description,
                    args_schema=descriptor.args_schema(),
                    metadata=metadata,
                )
            )
        return McpToolSnapshot(catalog=catalog, tools=tuple(wrappers))

    async def invalidate(self) -> None:
        """Invalidate all scope catalogs after a config revision change."""
        previous = self._snapshot.revision
        self._cache.clear()
        self._locks.clear()
        await self._transport.invalidate_revision(previous)

    async def replace_snapshot(self, snapshot: McpConfigSnapshot) -> None:
        """Swap configuration and invalidate old scoped discovery state."""
        previous = self._snapshot.revision
        self._snapshot = snapshot
        self._cache.clear()
        self._locks.clear()
        if previous != snapshot.revision:
            await self._transport.invalidate_revision(previous)

    async def close_scope(self, scope: McpScope) -> None:
        """Release transport resources for one owner/workspace/thread scope."""
        keys = [key for key in self._cache if key[1] == scope.key]
        for key in keys:
            self._cache.pop(key, None)
            self._locks.pop(key, None)
        await self._transport.close_scope(scope)

    async def aclose(self) -> None:
        """Close all transport resources and reject future discovery."""
        if self._closed:
            return
        self._closed = True
        self._cache.clear()
        self._locks.clear()
        await self._transport.aclose()

    @staticmethod
    def _validate_server_tools(
        server: McpServerConfig,
        tools: Sequence[McpToolDescriptor],
    ) -> tuple[McpToolDescriptor, ...]:
        names: set[str] = set()
        valid: list[McpToolDescriptor] = []
        prefix = f"{server.name}_"
        for tool in tools:
            if tool.server_name != server.name or not tool.name.startswith(prefix):
                raise ValueError(f"MCP tool is not safely prefixed for server '{server.name}'")
            if tool.name in names:
                raise ValueError(f"MCP tool name collision: {tool.name}")
            names.add(tool.name)
            valid.append(tool)
        return tuple(valid)


def _catalog_hash(revision: str, tools: Sequence[McpToolDescriptor]) -> str:
    payload = {
        "revision": revision,
        "tools": [
            {
                "tool_id": tool.tool_id,
                "name": tool.name,
                "schema_hash": tool.schema_hash,
                "remote_content": tool.remote_content,
            }
            for tool in sorted(tools, key=lambda item: item.tool_id)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "McpCatalogSnapshot",
    "McpConfigSnapshot",
    "McpManager",
    "McpScope",
    "McpServerConfig",
    "McpToolDescriptor",
    "McpToolSnapshot",
    "McpTransportPort",
]
