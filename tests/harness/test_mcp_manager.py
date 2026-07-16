"""Scoped MCP manager contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from sage_harness.mcp import (
    McpConfigSnapshot,
    McpManager,
    McpScope,
    McpServerConfig,
    McpToolDescriptor,
)


class FakeTransport:
    def __init__(self) -> None:
        self.discoveries: list[tuple[str, tuple[str, str, str]]] = []
        self.invocations: list[tuple[str, dict[str, object], tuple[str, str, str]]] = []
        self.closed_scopes: list[tuple[str, str, str]] = []
        self.invalidated: list[str] = []
        self.closed = False
        self.fail_servers: set[str] = set()

    async def discover(
        self,
        server: McpServerConfig,
        scope: McpScope,
    ) -> Sequence[McpToolDescriptor]:
        self.discoveries.append((server.name, scope.key))
        if server.name in self.fail_servers:
            raise RuntimeError("secret connection detail")
        return [
            McpToolDescriptor.from_schema(
                tool_id=f"{server.name}:lookup",
                server_name=server.name,
                name=f"{server.name}_lookup",
                original_name="lookup",
                description="Look up a bounded record",
                schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
                remote_content=server.remote_content,
            )
        ]

    async def invoke(
        self,
        tool: McpToolDescriptor,
        arguments: Mapping[str, object],
        scope: McpScope,
    ) -> object:
        self.invocations.append((tool.tool_id, dict(arguments), scope.key))
        return f"found:{arguments['query']}"

    async def close_scope(self, scope: McpScope) -> None:
        self.closed_scopes.append(scope.key)

    async def invalidate_revision(self, revision: str) -> None:
        self.invalidated.append(revision)

    async def aclose(self) -> None:
        self.closed = True


def _manager(transport: FakeTransport) -> McpManager:
    return McpManager(
        McpConfigSnapshot(
            revision="config-r1",
            servers=(
                McpServerConfig(name="docs", transport="stdio"),
                McpServerConfig(
                    name="remote",
                    transport="streamable_http",
                    status="unconfigured",
                ),
            ),
        ),
        transport,
    )


def test_manager_discovers_once_per_scope_and_builds_secret_free_wrappers() -> None:
    async def run() -> tuple[object, object, object]:
        transport = FakeTransport()
        manager = _manager(transport)
        scope = McpScope(owner_id="owner-1", workspace_id="workspace-1", thread_id="thread-1")
        first = await manager.load_tools(scope)
        second = await manager.load_tools(scope)
        result = await first.tools[0].ainvoke({"query": "sage"})
        return transport, first, (second, result)

    transport, first, paired = asyncio.run(run())
    second, result = paired

    assert transport.discoveries == [("docs", ("owner-1", "workspace-1", "thread-1"))]
    assert first.catalog is second.catalog
    assert first.catalog.servers[0].status == "connected"
    assert first.catalog.servers[1].status == "unconfigured"
    assert [tool.name for tool in first.tools] == ["docs_lookup"]
    assert first.tools[0].metadata["remote_content"] is True
    assert result == "found:sage"
    assert "secret" not in repr(first.catalog)


def test_manager_isolates_scopes_and_closes_only_the_requested_scope() -> None:
    async def run() -> FakeTransport:
        transport = FakeTransport()
        manager = _manager(transport)
        first = McpScope("owner-1", "workspace", "thread")
        second = McpScope("owner-2", "workspace", "thread")
        await manager.load_tools(first)
        await manager.load_tools(second)
        await manager.close_scope(first)
        await manager.load_tools(first)
        await manager.aclose()
        return transport

    transport = asyncio.run(run())

    assert transport.discoveries == [
        ("docs", ("owner-1", "workspace", "thread")),
        ("docs", ("owner-2", "workspace", "thread")),
        ("docs", ("owner-1", "workspace", "thread")),
    ]
    assert transport.closed_scopes == [("owner-1", "workspace", "thread")]
    assert transport.closed is True


def test_one_failed_server_degrades_without_exposing_its_exception() -> None:
    async def run() -> object:
        transport = FakeTransport()
        transport.fail_servers.add("docs")
        manager = _manager(transport)
        return await manager.load_tools(McpScope("owner", "workspace", "thread"))

    snapshot = asyncio.run(run())

    assert snapshot.tools == ()
    assert snapshot.catalog.servers[0].status == "error"
    assert "secret connection detail" not in repr(snapshot)


def test_config_replacement_invalidates_old_revision_and_catalog_hash() -> None:
    async def run() -> tuple[FakeTransport, str, str]:
        transport = FakeTransport()
        manager = _manager(transport)
        scope = McpScope("owner", "workspace", "thread")
        first = await manager.catalog(scope)
        await manager.replace_snapshot(
            McpConfigSnapshot(
                revision="config-r2",
                servers=(McpServerConfig(name="docs", transport="stdio"),),
            )
        )
        second = await manager.catalog(scope)
        return transport, first.catalog_hash, second.catalog_hash

    transport, first_hash, second_hash = asyncio.run(run())

    assert transport.invalidated == ["config-r1"]
    assert first_hash != second_hash


def test_invalid_unprefixed_tool_fails_closed_for_the_server() -> None:
    class InvalidTransport(FakeTransport):
        async def discover(
            self,
            server: McpServerConfig,
            scope: McpScope,
        ) -> Sequence[McpToolDescriptor]:
            _ = scope
            return [
                McpToolDescriptor.from_schema(
                    tool_id="docs:lookup",
                    server_name=server.name,
                    name="lookup",
                    original_name="lookup",
                    description="unsafe",
                    schema={"type": "object", "properties": {}},
                )
            ]

    async def run() -> object:
        manager = _manager(InvalidTransport())
        return await manager.catalog(McpScope("owner", "workspace", "thread"))

    catalog = asyncio.run(run())

    assert catalog.tools == ()
    assert catalog.servers[0].status == "error"


def test_configured_servers_are_discovered_concurrently_without_reordering() -> None:
    class ParallelTransport(FakeTransport):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.peak = 0
            self.both_started = asyncio.Event()

        async def discover(
            self,
            server: McpServerConfig,
            scope: McpScope,
        ) -> Sequence[McpToolDescriptor]:
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == 2:
                self.both_started.set()
            await self.both_started.wait()
            try:
                return await super().discover(server, scope)
            finally:
                self.active -= 1

    async def run() -> tuple[ParallelTransport, object]:
        transport = ParallelTransport()
        manager = McpManager(
            McpConfigSnapshot(
                revision="config-r1",
                servers=(
                    McpServerConfig(name="first", transport="stdio"),
                    McpServerConfig(name="second", transport="stdio"),
                ),
            ),
            transport,
            discovery_timeout_seconds=0.5,
        )
        catalog = await manager.catalog(McpScope("owner", "workspace", "thread"))
        await manager.aclose()
        return transport, catalog

    transport, catalog = asyncio.run(run())

    assert transport.peak == 2
    assert [server.name for server in catalog.servers] == ["first", "second"]
    assert [server.status for server in catalog.servers] == ["connected", "connected"]
