"""Application-scoped persistent MCP sessions with deterministic cleanup."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from typing import Any, Protocol, cast

from sage_harness import McpScope

McpSessionKey = tuple[str, str, tuple[str, str, str]]
logger = logging.getLogger(__name__)


class McpClientSession(Protocol):
    async def initialize(self) -> object: ...


McpSessionFactory = Callable[
    [Mapping[str, Any]],
    AbstractAsyncContextManager[McpClientSession],
]


@dataclass(frozen=True, slots=True)
class McpSessionPoolStats:
    active: int
    initializing: int


@dataclass(slots=True)
class _SessionOwner:
    loop: asyncio.AbstractEventLoop
    task: asyncio.Task[None]
    close_event: asyncio.Event
    ready: asyncio.Future[McpClientSession]


@dataclass(slots=True)
class _SessionEntry:
    owner: _SessionOwner
    session: McpClientSession


class ScopedMcpSessionPool:
    """Reuse sessions by revision, server, and full Sage tenant/thread scope.

    MCP stdio contexts are backed by anyio cancel scopes. The dedicated owner
    task enters and exits each context so cleanup never crosses task boundaries.
    """

    def __init__(
        self,
        *,
        session_factory: McpSessionFactory | None = None,
        max_sessions: int = 64,
    ) -> None:
        if max_sessions <= 0:
            raise ValueError("MCP max_sessions must be positive")
        self._session_factory = session_factory or _default_session_factory
        self._max_sessions = max_sessions
        self._entries: OrderedDict[McpSessionKey, _SessionEntry] = OrderedDict()
        self._inflight: dict[McpSessionKey, _SessionOwner] = {}
        self._lock = threading.Lock()
        self._closed = False

    def stats(self) -> McpSessionPoolStats:
        """Return aggregate counts without exposing scope or connection data."""
        with self._lock:
            return McpSessionPoolStats(
                active=len(self._entries),
                initializing=len(self._inflight),
            )

    def has_session(
        self,
        *,
        revision: str,
        server_name: str,
        scope: McpScope,
    ) -> bool:
        """Return whether a usable session is still registered for this key."""
        key = (revision, server_name, scope.key)
        with self._lock:
            entry = self._entries.get(key)
            return entry is not None and not entry.owner.task.done()

    async def get_session(
        self,
        *,
        revision: str,
        server_name: str,
        scope: McpScope,
        connection: Mapping[str, Any],
    ) -> McpClientSession:
        """Get or initialize one session without retaining public credentials."""
        key = (revision, server_name, scope.key)
        loop = asyncio.get_running_loop()
        evicted: list[_SessionOwner] = []
        join: asyncio.Future[McpClientSession] | None = None
        owner: _SessionOwner | None = None

        with self._lock:
            if self._closed:
                raise RuntimeError("MCP session pool is closed")
            entry = self._entries.get(key)
            if entry is not None:
                if entry.owner.loop is loop and not entry.owner.task.done():
                    self._entries.move_to_end(key)
                    return entry.session
                self._entries.pop(key)
                evicted.append(entry.owner)

            existing = self._inflight.get(key)
            if existing is not None:
                if existing.loop is loop and not existing.task.done():
                    join = existing.ready
                else:
                    self._inflight.pop(key)
                    evicted.append(existing)

            if join is None:
                ready = loop.create_future()
                close_event = asyncio.Event()
                task = loop.create_task(
                    self._run_session(dict(connection), ready, close_event),
                    name=f"sage-mcp:{server_name}",
                )
                owner = _SessionOwner(loop, task, close_event, ready)
                self._inflight[key] = owner

        for stale in evicted:
            await self._shutdown_owner(stale, cancel=not stale.ready.done())
        if join is not None:
            return await asyncio.shield(join)
        if owner is None:
            raise RuntimeError("MCP session owner was not created")

        try:
            session = await asyncio.shield(owner.ready)
        except BaseException:
            owner_failed = (
                owner.ready.done()
                and not owner.ready.cancelled()
                and owner.ready.exception() is not None
            )
            if not owner_failed:
                await self._shutdown_owner(owner, cancel=True)
            else:
                await self._wait_owner(owner)
            with self._lock:
                if self._inflight.get(key) is owner:
                    self._inflight.pop(key)
            raise

        registered_evictions: list[_SessionOwner] = []
        with self._lock:
            still_current = self._inflight.get(key) is owner and not self._closed
            if still_current:
                self._inflight.pop(key)
                while len(self._entries) >= self._max_sessions:
                    _, evicted_entry = self._entries.popitem(last=False)
                    registered_evictions.append(evicted_entry.owner)
                self._entries[key] = _SessionEntry(owner=owner, session=session)
        if not still_current:
            await self._shutdown_owner(owner)
            raise asyncio.CancelledError("MCP session was closed during initialization")
        for stale in registered_evictions:
            await self._shutdown_owner(stale)
        return session

    async def close_session(
        self,
        *,
        revision: str,
        server_name: str,
        scope: McpScope,
    ) -> None:
        key = (revision, server_name, scope.key)
        entries, inflight = self._pop_matching(lambda item: item == key)
        await self._close_owners(entries, inflight)

    async def close_scope(self, scope: McpScope) -> None:
        entries, inflight = self._pop_matching(lambda item: item[2] == scope.key)
        await self._close_owners(entries, inflight)

    async def invalidate_revision(self, revision: str) -> None:
        entries, inflight = self._pop_matching(lambda item: item[0] == revision)
        await self._close_owners(entries, inflight)

    async def aclose(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            entries = [entry.owner for entry in self._entries.values()]
            inflight = list(self._inflight.values())
            self._entries.clear()
            self._inflight.clear()
        await self._close_owners(entries, inflight)

    async def _run_session(
        self,
        connection: Mapping[str, Any],
        ready: asyncio.Future[McpClientSession],
        close_event: asyncio.Event,
    ) -> None:
        context = self._session_factory(connection)
        entered = False
        try:
            session = await context.__aenter__()
            entered = True
            await session.initialize()
            if not ready.done():
                ready.set_result(session)
            await close_event.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
        finally:
            if entered:
                try:
                    await context.__aexit__(None, None, None)
                except Exception as exc:
                    logger.warning(
                        "MCP session cleanup failed (%s)",
                        type(exc).__name__,
                    )

    def _pop_matching(
        self,
        predicate: Callable[[McpSessionKey], bool],
    ) -> tuple[list[_SessionOwner], list[_SessionOwner]]:
        with self._lock:
            entry_keys = [key for key in self._entries if predicate(key)]
            entries = [self._entries.pop(key).owner for key in entry_keys]
            inflight_keys = [key for key in self._inflight if predicate(key)]
            inflight = [self._inflight.pop(key) for key in inflight_keys]
        return entries, inflight

    async def _close_owners(
        self,
        entries: list[_SessionOwner],
        inflight: list[_SessionOwner],
    ) -> None:
        for owner in entries:
            await self._shutdown_owner(owner)
        for owner in inflight:
            await self._shutdown_owner(owner, cancel=True)

    async def _shutdown_owner(self, owner: _SessionOwner, *, cancel: bool = False) -> None:
        if owner.loop.is_closed():
            return
        current = asyncio.get_running_loop()
        if owner.loop is current:
            owner.close_event.set()
            if cancel:
                owner.task.cancel()
            await self._wait_owner(owner)
            return
        if owner.loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._shutdown_on_owner_loop(owner, cancel=cancel),
                owner.loop,
            )
            await asyncio.wrap_future(future)
            return
        owner.loop.call_soon_threadsafe(owner.close_event.set)
        if cancel:
            owner.loop.call_soon_threadsafe(owner.task.cancel)

    async def _shutdown_on_owner_loop(
        self,
        owner: _SessionOwner,
        *,
        cancel: bool,
    ) -> None:
        owner.close_event.set()
        if cancel:
            owner.task.cancel()
        await self._wait_owner(owner)

    @staticmethod
    async def _wait_owner(owner: _SessionOwner) -> None:
        with suppress(Exception, asyncio.CancelledError):
            await asyncio.shield(owner.task)


def _default_session_factory(
    connection: Mapping[str, Any],
) -> AbstractAsyncContextManager[McpClientSession]:
    from langchain_mcp_adapters.sessions import create_session

    return cast(
        AbstractAsyncContextManager[McpClientSession],
        create_session(cast(Any, dict(connection))),
    )


__all__ = [
    "McpClientSession",
    "McpSessionFactory",
    "McpSessionPoolStats",
    "ScopedMcpSessionPool",
]
