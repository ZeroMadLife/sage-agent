"""Server-owned coding runs with durable replay and live subscriptions."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from core.coding.persistence.session_event_journal import (
    TERMINAL_STATUSES,
    SessionEvent,
    SessionEventJournal,
    SessionRunLeaseConflictError,
)

_PROCESS_INSTANCE_ID = f"process-{uuid.uuid4()}"


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

    def __init__(
        self,
        journal: SessionEventJournal,
        *,
        owner_id: str | None = None,
        subscriber_queue_size: int = 256,
    ) -> None:
        if subscriber_queue_size < 1:
            raise ValueError("subscriber_queue_size must be positive")
        self.journal = journal
        self.owner_id = owner_id or _PROCESS_INSTANCE_ID
        self._subscriber_queue_size = subscriber_queue_size
        self._state_lock = asyncio.Lock()
        self._publish_lock = asyncio.Lock()
        self._active_run_id: str | None = None
        self._active_task: asyncio.Task[None] | None = None
        self._subscribers: set[asyncio.Queue[SessionEvent]] = set()
        self._last_broadcast_sequence = 0

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
            begin_task = asyncio.create_task(self._begin_run(run_id))
            try:
                await asyncio.shield(begin_task)
            except asyncio.CancelledError:
                try:
                    await _wait_through_cancellation(begin_task)
                except Exception:
                    pass
                else:
                    cleanup_task = asyncio.create_task(
                        self._persist(
                            run_id=run_id,
                            event=RunEvent(
                                kind="terminal",
                                status="cancelled",
                                payload={"event": "run_cancelled"},
                            ),
                        )
                    )
                    await _wait_through_cancellation(cleanup_task)
                close_task = asyncio.create_task(_close_unowned_stream(event_stream))
                await _wait_through_cancellation(close_task)
                raise
            except SessionRunLeaseConflictError as exc:
                await _close_unowned_stream(event_stream)
                raise ActiveRunConflictError(str(exc)) from exc
            except Exception:
                await _close_unowned_stream(event_stream)
                raise
            self._active_run_id = run_id
            task = asyncio.create_task(
                self._consume(run_id, event_stream), name=f"sage-run-{run_id}"
            )
            self._active_task = task
            return task

    async def _begin_run(self, run_id: str) -> SessionEvent:
        async with self._publish_lock:
            stored = await asyncio.to_thread(
                self.journal.begin_run, run_id, owner_id=self.owner_id
            )
            self._broadcast(stored)
            return stored

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
        queue: asyncio.Queue[SessionEvent] = asyncio.Queue(
            maxsize=self._subscriber_queue_size
        )
        cursor = after
        async with self._publish_lock:
            high_water = await asyncio.to_thread(self.journal.latest_sequence)
            self._subscribers.add(queue)
        try:
            while cursor < high_water:
                page = await asyncio.to_thread(self.journal.replay, after=cursor, limit=500)
                items = tuple(item for item in page.items if item.sequence <= high_water)
                if not items:
                    break
                for event in items:
                    cursor = event.sequence
                    yield event
            while True:
                event = await queue.get()
                if event.sequence <= cursor:
                    continue
                if event.sequence > cursor + 1:
                    target = event.sequence
                    while cursor < target:
                        page = await asyncio.to_thread(
                            self.journal.replay, after=cursor, limit=500
                        )
                        items = tuple(item for item in page.items if item.sequence <= target)
                        if not items:
                            raise RunCoordinatorError("journal sequence gap could not be repaired")
                        for repaired in items:
                            cursor = repaired.sequence
                            yield repaired
                else:
                    cursor = event.sequence
                    yield event
        finally:
            self._subscribers.discard(queue)

    async def recover_interrupted_runs(self) -> tuple[str, ...]:
        """Close abandoned starts as retryable interruptions without resuming them."""
        recovered: list[str] = []
        async with self._publish_lock:
            lease_event = await asyncio.to_thread(
                self.journal.recover_run_lease, recovery_owner_id=self.owner_id
            )
            if lease_event is not None:
                self._broadcast(lease_event)
                recovered.append(lease_event.run_id)
        run_ids = await asyncio.to_thread(self.journal.unfinished_run_ids)
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
                    self.journal.append_terminal_and_release,
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
            self._broadcast(stored)
            return stored

    def _broadcast(self, stored: SessionEvent) -> None:
        if stored.sequence <= self._last_broadcast_sequence:
            return
        self._last_broadcast_sequence = stored.sequence
        for queue in tuple(self._subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(stored)


async def _wait_through_cancellation(task: asyncio.Task[SessionEvent] | asyncio.Task[None]) -> None:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    await task


async def _close_unowned_stream(stream: AsyncIterator[RunEvent | Mapping[str, Any]]) -> None:
    close = getattr(stream, "aclose", None)
    if close is not None:
        await close()


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
