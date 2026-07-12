"""Server-owned coding runs with durable replay and live subscriptions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from core.coding.persistence.session_event_journal import (
    TERMINAL_STATUSES,
    SessionEvent,
    SessionEventJournal,
)


class RunCoordinatorError(RuntimeError):
    """Base error for coordinated runs."""


class ActiveRunConflictError(RunCoordinatorError):
    """A session already owns an active run."""


@dataclass(frozen=True, slots=True)
class RunEvent:
    kind: str
    status: str
    payload: dict[str, Any]
    event_id: str | None = None
    timestamp: str | None = None


class RunCoordinator:
    """Own run tasks independently from any individual subscriber."""

    def __init__(self, journal: SessionEventJournal) -> None:
        self.journal = journal
        self._state_lock = asyncio.Lock()
        self._publish_lock = asyncio.Lock()
        self._active_run_id: str | None = None
        self._active_task: asyncio.Task[None] | None = None
        self._subscribers: set[asyncio.Queue[SessionEvent]] = set()
        self._published_event_ids: set[str] = set()

    @property
    def active_run_id(self) -> str | None:
        return self._active_run_id

    async def start_run(
        self,
        run_id: str,
        event_stream: AsyncIterator[RunEvent | Mapping[str, Any]],
    ) -> asyncio.Task[None]:
        """Start consuming an event stream as the session's active run."""
        async with self._state_lock:
            if self._active_run_id is not None:
                raise ActiveRunConflictError(
                    f"session already has active run {self._active_run_id}"
                )
            await self._persist(
                run_id=run_id,
                event=RunEvent(
                    kind="system", status="running", payload={"event": "run_started"}
                ),
            )
            self._active_run_id = run_id
            task = asyncio.create_task(
                self._consume(run_id, event_stream), name=f"sage-run-{run_id}"
            )
            self._active_task = task
            return task

    async def cancel(self, run_id: str) -> bool:
        """Cancel the matching active task; subscribers are unaffected."""
        async with self._state_lock:
            if self._active_run_id != run_id or self._active_task is None:
                return False
            task = self._active_task
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await self._persist(
            run_id=run_id,
            event=RunEvent(
                kind="terminal", status="cancelled", payload={"event": "run_cancelled"}
            ),
        )
        async with self._state_lock:
            if self._active_task is task:
                self._active_run_id = None
                self._active_task = None
        return True

    async def subscribe(self, *, after: int = 0) -> AsyncIterator[SessionEvent]:
        """Replay history and then deliver live events without a race window."""
        queue: asyncio.Queue[SessionEvent] = asyncio.Queue()
        history: list[SessionEvent] = []
        cursor = after
        async with self._publish_lock:
            while True:
                page = await asyncio.to_thread(self.journal.replay, after=cursor, limit=500)
                history.extend(page.items)
                cursor = page.next_cursor
                if not page.has_more:
                    break
            self._subscribers.add(queue)
        try:
            for event in history:
                yield event
            while True:
                event = await queue.get()
                if event.sequence <= cursor:
                    continue
                cursor = event.sequence
                yield event
        finally:
            self._subscribers.discard(queue)

    async def recover_interrupted_runs(self) -> tuple[str, ...]:
        """Close abandoned starts as retryable interruptions without resuming them."""
        run_ids = await asyncio.to_thread(self.journal.unfinished_run_ids)
        recovered: list[str] = []
        for run_id in run_ids:
            event = RunEvent(
                kind="terminal",
                status="interrupted",
                payload={"event": "run_interrupted", "retryable": True},
            )
            await self._persist(run_id=run_id, event=event)
            recovered.append(run_id)
        return tuple(recovered)

    async def _consume(
        self,
        run_id: str,
        event_stream: AsyncIterator[RunEvent | Mapping[str, Any]],
    ) -> None:
        terminal_seen = False
        try:
            async for raw_event in event_stream:
                event = _coerce_event(raw_event)
                if terminal_seen:
                    break
                await asyncio.shield(self._persist(run_id=run_id, event=event))
                if event.kind == "terminal":
                    terminal_seen = True
                    break
            if not terminal_seen:
                await self._persist(
                    run_id=run_id,
                    event=RunEvent(
                        kind="terminal",
                        status="completed",
                        payload={"event": "run_completed"},
                    ),
                )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._persist(
                    run_id=run_id,
                    event=RunEvent(
                        kind="terminal",
                        status="cancelled",
                        payload={"event": "run_cancelled"},
                    ),
                )
            )
            raise
        except Exception as exc:
            await self._persist(
                run_id=run_id,
                event=RunEvent(
                    kind="terminal",
                    status="error",
                    payload={"event": "run_error", "error_type": type(exc).__name__},
                ),
            )
            raise
        finally:
            async with self._state_lock:
                if self._active_run_id == run_id:
                    self._active_run_id = None
                    self._active_task = None

    async def _persist(self, *, run_id: str, event: RunEvent) -> SessionEvent:
        async with self._publish_lock:
            if event.kind == "terminal":
                if event.status not in TERMINAL_STATUSES:
                    raise ValueError("terminal event requires a terminal status")
                stored = await asyncio.to_thread(
                    self.journal.append_terminal_once,
                    run_id=run_id,
                    status=event.status,
                    payload=event.payload,
                    event_id=event.event_id,
                    timestamp=event.timestamp,
                )
            else:
                stored = await asyncio.to_thread(
                    self.journal.append,
                    run_id=run_id,
                    kind=event.kind,
                    status=event.status,
                    payload=event.payload,
                    event_id=event.event_id,
                    timestamp=event.timestamp,
                )
            if stored.event_id not in self._published_event_ids:
                self._published_event_ids.add(stored.event_id)
                for queue in tuple(self._subscribers):
                    queue.put_nowait(stored)
            return stored


def _coerce_event(raw_event: RunEvent | Mapping[str, Any]) -> RunEvent:
    if isinstance(raw_event, RunEvent):
        return raw_event
    payload = raw_event.get("payload", {})
    if not isinstance(payload, dict):
        raise TypeError("run event payload must be an object")
    event_id = raw_event.get("event_id")
    timestamp = raw_event.get("timestamp")
    return RunEvent(
        kind=str(raw_event.get("kind", "")),
        status=str(raw_event.get("status", "")),
        payload=payload,
        event_id=str(event_id) if event_id is not None else None,
        timestamp=str(timestamp) if timestamp is not None else None,
    )
