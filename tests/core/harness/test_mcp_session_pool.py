"""Scoped persistent MCP session lifecycle tests."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from sage_harness import McpScope

from core.harness.mcp_session_pool import McpClientSession, ScopedMcpSessionPool


class FakeSession:
    def __init__(self, name: str) -> None:
        self.name = name
        self.initialize_count = 0

    async def initialize(self) -> None:
        self.initialize_count += 1


class FakeSessionContext(AbstractAsyncContextManager[McpClientSession]):
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.enter_task: asyncio.Task[Any] | None = None
        self.exit_task: asyncio.Task[Any] | None = None

    async def __aenter__(self) -> McpClientSession:
        self.enter_task = asyncio.current_task()
        return self.session

    async def __aexit__(self, *args: object) -> None:
        self.exit_task = asyncio.current_task()


class FakeSessionFactory:
    def __init__(self) -> None:
        self.contexts: list[FakeSessionContext] = []

    def __call__(self, connection: object) -> FakeSessionContext:
        _ = connection
        context = FakeSessionContext(FakeSession(f"session-{len(self.contexts) + 1}"))
        self.contexts.append(context)
        return context


def test_pool_reuses_one_session_and_exits_on_the_owner_task() -> None:
    async def run() -> tuple[FakeSessionFactory, object, object]:
        factory = FakeSessionFactory()
        pool = ScopedMcpSessionPool(session_factory=factory)
        scope = McpScope("owner", "workspace", "thread")
        first, second = await asyncio.gather(
            pool.get_session(revision="r1", server_name="docs", scope=scope, connection={}),
            pool.get_session(revision="r1", server_name="docs", scope=scope, connection={}),
        )
        await pool.aclose()
        return factory, first, second

    factory, first, second = asyncio.run(run())

    assert first is second
    assert len(factory.contexts) == 1
    assert factory.contexts[0].session.initialize_count == 1
    assert factory.contexts[0].enter_task is factory.contexts[0].exit_task


def test_pool_isolates_full_scope_and_closes_only_the_requested_scope() -> None:
    async def run() -> tuple[FakeSessionFactory, object]:
        factory = FakeSessionFactory()
        pool = ScopedMcpSessionPool(session_factory=factory)
        first = McpScope("owner-a", "workspace", "thread")
        second = McpScope("owner-b", "workspace", "thread")
        session_a = await pool.get_session(
            revision="r1", server_name="docs", scope=first, connection={}
        )
        session_b = await pool.get_session(
            revision="r1", server_name="docs", scope=second, connection={}
        )
        await pool.close_scope(first)
        reused_b = await pool.get_session(
            revision="r1", server_name="docs", scope=second, connection={}
        )
        stats = pool.stats()
        await pool.aclose()
        return factory, (session_a, session_b, reused_b, stats)

    factory, result = asyncio.run(run())
    session_a, session_b, reused_b, stats = result

    assert session_a is not session_b
    assert session_b is reused_b
    assert factory.contexts[0].exit_task is not None
    assert stats.active == 1
    assert stats.initializing == 0


def test_pool_invalidates_only_matching_revision_and_reconnects() -> None:
    async def run() -> tuple[FakeSessionFactory, object]:
        factory = FakeSessionFactory()
        pool = ScopedMcpSessionPool(session_factory=factory)
        scope = McpScope("owner", "workspace", "thread")
        first = await pool.get_session(
            revision="r1", server_name="docs", scope=scope, connection={}
        )
        current = await pool.get_session(
            revision="r2", server_name="docs", scope=scope, connection={}
        )
        await pool.invalidate_revision("r1")
        current_again = await pool.get_session(
            revision="r2", server_name="docs", scope=scope, connection={}
        )
        replacement = await pool.get_session(
            revision="r1", server_name="docs", scope=scope, connection={}
        )
        await pool.aclose()
        return factory, (first, current, current_again, replacement)

    factory, result = asyncio.run(run())
    first, current, current_again, replacement = result

    assert first is not replacement
    assert current is current_again
    assert len(factory.contexts) == 3


def test_pool_rejects_new_sessions_after_close() -> None:
    async def run() -> None:
        pool = ScopedMcpSessionPool(session_factory=FakeSessionFactory())
        await pool.aclose()
        with pytest.raises(RuntimeError, match="closed"):
            await pool.get_session(
                revision="r1",
                server_name="docs",
                scope=McpScope("owner", "workspace", "thread"),
                connection={},
            )

    asyncio.run(run())


def test_concurrent_initialization_enforces_lru_capacity_after_sessions_are_ready() -> None:
    async def run() -> tuple[FakeSessionFactory, object]:
        factory = FakeSessionFactory()
        pool = ScopedMcpSessionPool(session_factory=factory, max_sessions=2)
        scopes = [McpScope("owner", "workspace", f"thread-{index}") for index in range(3)]
        await asyncio.gather(
            *(
                pool.get_session(
                    revision="r1",
                    server_name="docs",
                    scope=scope,
                    connection={},
                )
                for scope in scopes
            )
        )
        stats = pool.stats()
        presence = [
            pool.has_session(revision="r1", server_name="docs", scope=scope) for scope in scopes
        ]
        await pool.aclose()
        return factory, (stats, presence)

    factory, result = asyncio.run(run())
    stats, presence = result

    assert stats.active == 2
    assert stats.initializing == 0
    assert presence.count(True) == 2
    assert sum(context.exit_task is not None for context in factory.contexts) == 3


def test_cancelled_creation_does_not_leave_an_owner_task_or_session() -> None:
    async def run() -> tuple[FakeSessionContext, object]:
        task_box: dict[str, asyncio.Task[McpClientSession]] = {}

        class CancellingSession(FakeSession):
            async def initialize(self) -> None:
                await super().initialize()
                asyncio.get_running_loop().call_soon(task_box["task"].cancel)

        context = FakeSessionContext(CancellingSession("cancelled"))
        pool = ScopedMcpSessionPool(session_factory=lambda _connection: context)
        task = asyncio.create_task(
            pool.get_session(
                revision="r1",
                server_name="docs",
                scope=McpScope("owner", "workspace", "thread"),
                connection={},
            )
        )
        task_box["task"] = task
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        stats = pool.stats()
        await pool.aclose()
        return context, stats

    context, stats = asyncio.run(run())

    assert context.exit_task is not None
    assert context.enter_task is context.exit_task
    assert stats.active == 0
    assert stats.initializing == 0
