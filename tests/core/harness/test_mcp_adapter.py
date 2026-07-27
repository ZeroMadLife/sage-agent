"""MCP catalog boundary tests for the DeerFlow-compatible runtime."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from typing import cast

from langchain_core.tools import StructuredTool
from sage_harness import McpConfigSnapshot, McpManager, McpScope, McpServerConfig

from core.harness.mcp_adapter import (
    ConfiguredMcpCatalog,
    LangChainMcpTransport,
    mcp_catalog_event,
)
from core.harness.mcp_session_pool import McpClientSession, ScopedMcpSessionPool


def test_mcp_catalog_exposes_status_without_connection_secrets() -> None:
    catalog = ConfiguredMcpCatalog(
        {
            "docs": {
                "transport": "stdio",
                "command": "docs-server",
                "env": {"DOCS_API_KEY": "private-docs-key"},
            }
        }
    )

    servers = asyncio.run(catalog.list_servers())
    rendered = repr(servers)

    assert {server.name for server in servers} == {"docs"}
    assert all(server.status == "configured" for server in servers)
    assert "private-docs-key" not in rendered
    assert "command" not in rendered
    assert "env" not in rendered


def test_empty_mcp_catalog_does_not_connect() -> None:
    catalog = ConfiguredMcpCatalog({})

    servers = asyncio.run(catalog.list_servers())

    assert servers == ()


def test_mcp_catalog_event_contains_only_sanitized_metadata() -> None:
    catalog = ConfiguredMcpCatalog(
        {"docs": {"transport": "stdio", "command": "docs-server", "env": {"KEY": "secret"}}}
    )

    event = asyncio.run(mcp_catalog_event(catalog, session_id="s1", run_id="r1"))

    assert event.payload["type"] == "mcp_catalog_updated"
    assert event.event_id == "harness:r1:mcp-catalog"
    rendered = repr(event.payload)
    assert "secret" not in rendered
    assert "command" not in rendered
    assert "args" not in rendered
    assert "env" not in rendered


def test_live_mcp_manager_discovers_prefixed_tools_without_public_secrets() -> None:
    class FakeClient:
        async def get_tools(self, *, server_name: str):
            assert server_name == "docs"
            return [
                StructuredTool.from_function(
                    coroutine=lambda query: query,
                    name="docs_search",
                    description="Search documentation",
                )
            ]

    transport = LangChainMcpTransport(
        {"docs": {"transport": "stdio", "command": "docs-server"}},
        revision="r1",
        client_factory=lambda connections: FakeClient(),
    )
    manager = McpManager(
        McpConfigSnapshot(
            revision="r1",
            servers=(McpServerConfig(name="docs", transport="stdio", remote_content=True),),
        ),
        transport,
    )

    async def run():
        from sage_harness import McpScope

        return await manager.load_tools(McpScope("owner", "workspace", "thread"))

    snapshot = asyncio.run(run())

    assert [tool.name for tool in snapshot.tools] == ["docs_search"]
    statuses = {server.name: server.status for server in snapshot.catalog.servers}
    assert statuses["docs"] == "connected"


def test_live_transport_reuses_scoped_session_and_reconnects_after_transport_failure() -> None:
    class Session:
        def __init__(self, index: int) -> None:
            self.index = index
            self.calls = 0

        async def initialize(self) -> None:
            return None

    class Context(AbstractAsyncContextManager[Session]):
        def __init__(self, session: Session) -> None:
            self.session = session
            self.closed = False

        async def __aenter__(self) -> Session:
            return self.session

        async def __aexit__(self, *args: object) -> None:
            self.closed = True

    async def run() -> tuple[list[Session], object, object]:
        sessions: list[Session] = []

        def session_context(_connection: object) -> Context:
            session = Session(len(sessions) + 1)
            sessions.append(session)
            return Context(session)

        pool = ScopedMcpSessionPool(session_factory=session_context)

        async def load_tools(session: McpClientSession, server_name: str):
            assert server_name == "docs"
            typed = cast(Session, session)

            async def search(query: str) -> str:
                typed.calls += 1
                if typed.index == 1:
                    raise RuntimeError("transport disconnected")
                return f"session-{typed.index}:{query}"

            return [
                StructuredTool.from_function(
                    coroutine=search,
                    name="docs_search",
                    description="Search documentation",
                )
            ]

        transport = LangChainMcpTransport(
            {"docs": {"transport": "stdio", "command": "docs-server"}},
            revision="r1",
            session_pool=pool,
            tool_loader=load_tools,
        )
        scope = McpScope("owner", "workspace", "thread")
        server = McpServerConfig(name="docs", transport="stdio", remote_content=False)
        descriptors = await transport.discover(server, scope)
        assert await transport.discover(server, scope) == descriptors
        try:
            await transport.invoke(descriptors[0], {"query": "west-lake"}, scope)
        except RuntimeError as exc:
            assert str(exc) == "transport disconnected"
        await transport.discover(server, scope)
        result = await transport.invoke(descriptors[0], {"query": "west-lake"}, scope)
        await transport.aclose()
        return sessions, result, descriptors

    sessions, result, descriptors = asyncio.run(run())

    assert result == "session-2:west-lake"
    assert len(sessions) == 2
    assert sessions[0].calls == 1
    assert sessions[1].calls == 1
    assert descriptors[0].name == "docs_search"


def test_live_transport_rediscovers_a_session_evicted_by_the_lru_pool() -> None:
    class Session:
        def __init__(self, index: int) -> None:
            self.index = index

        async def initialize(self) -> None:
            return None

    class Context(AbstractAsyncContextManager[Session]):
        def __init__(self, session: Session) -> None:
            self.session = session

        async def __aenter__(self) -> Session:
            return self.session

        async def __aexit__(self, *args: object) -> None:
            return None

    async def run() -> tuple[list[Session], object]:
        sessions: list[Session] = []

        def session_context(_connection: object) -> Context:
            session = Session(len(sessions) + 1)
            sessions.append(session)
            return Context(session)

        async def load_tools(session: McpClientSession, _server_name: str):
            typed = cast(Session, session)

            async def search(query: str) -> str:
                return f"session-{typed.index}:{query}"

            return [
                StructuredTool.from_function(
                    coroutine=search,
                    name="docs_search",
                    description="Search documentation",
                )
            ]

        transport = LangChainMcpTransport(
            {"docs": {"transport": "stdio", "command": "docs-server"}},
            revision="r1",
            session_pool=ScopedMcpSessionPool(
                session_factory=session_context,
                max_sessions=1,
            ),
            tool_loader=load_tools,
        )
        server = McpServerConfig(name="docs", transport="stdio", remote_content=False)
        first_scope = McpScope("owner", "workspace", "thread-1")
        second_scope = McpScope("owner", "workspace", "thread-2")
        first_tools = await transport.discover(server, first_scope)
        await transport.discover(server, second_scope)
        result = await transport.invoke(first_tools[0], {"query": "west-lake"}, first_scope)
        await transport.aclose()
        return sessions, result

    sessions, result = asyncio.run(run())

    assert len(sessions) == 3
    assert result == "session-3:west-lake"
