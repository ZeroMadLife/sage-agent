"""Server-owned coding runs with durable replay and live subscriptions."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from core.coding.persistence.session_event_journal import (
    TERMINAL_STATUSES,
    BeginRunResult,
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
        owner_pid: int | None = None,
        subscriber_queue_size: int = 256,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        if subscriber_queue_size < 1:
            raise ValueError("subscriber_queue_size must be positive")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self.journal = journal
        self.owner_id = owner_id or _PROCESS_INSTANCE_ID
        self.owner_pid = os.getpid() if owner_pid is None else owner_pid
        self._subscriber_queue_size = subscriber_queue_size
        self._poll_interval_seconds = poll_interval_seconds
        self._state_lock = asyncio.Lock()
        self._publish_lock = asyncio.Lock()
        self._active_run_id: str | None = None
        self._active_task: asyncio.Task[None] | None = None
        self._active_fencing_token: int | None = None
        self._suspend_on_cancel: set[str] = set()
        self._subscribers: set[asyncio.Queue[SessionEvent]] = set()
        self._last_broadcast_sequence = 0

    @property
    def active_run_id(self) -> str | None:
        return self._active_run_id

    async def start_run(
        self,
        run_id: str,
        event_stream: AsyncIterator[RunEvent | Mapping[str, Any]],
        *,
        surface_context: Mapping[str, Any] | None = None,
    ) -> asyncio.Task[None]:
        """Start consuming an event stream as the session's active run."""
        frozen_surface_context = (
            deepcopy(dict(surface_context)) if surface_context is not None else None
        )
        async with self._state_lock:
            if self._active_run_id is not None:
                raise ActiveRunConflictError(
                    f"session already has active run {self._active_run_id}"
                )
            recoverable_approval = await asyncio.to_thread(
                self.journal.recoverable_approval
            )
            if recoverable_approval is not None:
                blocked_run_id = str(recoverable_approval.get("run_id", "unknown"))
                await _close_unowned_stream(event_stream)
                raise ActiveRunConflictError(
                    f"session has resumable approval for run {blocked_run_id}"
                )
            begin_task = asyncio.create_task(
                self._begin_run(run_id, surface_context=frozen_surface_context)
            )
            try:
                await asyncio.shield(begin_task)
            except asyncio.CancelledError:
                try:
                    await _wait_through_cancellation(begin_task)
                except Exception:
                    pass
                else:
                    begin_result = begin_task.result()
                    cleanup_task = asyncio.create_task(
                        self._persist(
                            run_id=run_id,
                            event=RunEvent(
                                kind="terminal",
                                status="cancelled",
                                payload={"event": "run_cancelled"},
                            ),
                            fencing_token=begin_result.fencing_token,
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
            begin_result = begin_task.result()
            self._active_run_id = run_id
            self._active_fencing_token = begin_result.fencing_token
            task = asyncio.create_task(
                self._consume(run_id, begin_result.fencing_token, event_stream),
                name=f"sage-run-{run_id}",
            )
            self._active_task = task
            return task

    async def _begin_run(
        self,
        run_id: str,
        *,
        surface_context: Mapping[str, Any] | None = None,
    ) -> BeginRunResult:
        async with self._publish_lock:
            kwargs: dict[str, Any] = {
                "owner_id": self.owner_id,
                "owner_pid": self.owner_pid,
            }
            if surface_context is not None:
                kwargs["surface_context"] = surface_context
            stored = await asyncio.to_thread(self.journal.begin_run, run_id, **kwargs)
            self._broadcast(stored.event)
            return stored

    async def start_existing_run(
        self,
        run_id: str,
        event_stream: AsyncIterator[RunEvent | Mapping[str, Any]],
    ) -> asyncio.Task[None]:
        """Resume an existing nonterminal run without duplicating run_started."""
        async with self._state_lock:
            if self._active_run_id is not None:
                raise ActiveRunConflictError(
                    f"session already has active run {self._active_run_id}"
                )
            begin_task = asyncio.create_task(
                asyncio.to_thread(
                    self.journal.resume_run,
                    run_id,
                    owner_id=self.owner_id,
                    owner_pid=self.owner_pid,
                )
            )
            try:
                await asyncio.shield(begin_task)
            except asyncio.CancelledError:
                try:
                    await _wait_through_cancellation(begin_task)
                except Exception:
                    pass
                else:
                    fencing_token = begin_task.result()
                    release_task = asyncio.create_task(
                        asyncio.to_thread(
                            self.journal.release_run_lease,
                            run_id,
                            owner_id=self.owner_id,
                            fencing_token=fencing_token,
                        )
                    )
                    await _wait_through_cancellation(release_task)
                close_task = asyncio.create_task(_close_unowned_stream(event_stream))
                await _wait_through_cancellation(close_task)
                raise
            except SessionRunLeaseConflictError as exc:
                await _close_unowned_stream(event_stream)
                raise ActiveRunConflictError(str(exc)) from exc
            except Exception:
                await _close_unowned_stream(event_stream)
                raise
            fencing_token = begin_task.result()
            self._active_run_id = run_id
            self._active_fencing_token = fencing_token
            task = asyncio.create_task(
                self._consume(run_id, fencing_token, event_stream),
                name=f"sage-run-resume-{run_id}",
            )
            self._active_task = task
            return task

    async def cancel(self, run_id: str) -> bool:
        """Cancel the matching active task; subscribers are unaffected."""
        async with self._state_lock:
            if self._active_run_id != run_id or self._active_task is None:
                return False
            task = self._active_task
            fencing_token = self._active_fencing_token
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        if await asyncio.to_thread(self.journal.active_run_id) == run_id:
            await self._persist(
                run_id=run_id,
                event=RunEvent(
                    kind="terminal", status="cancelled", payload={"event": "run_cancelled"}
                ),
                fencing_token=fencing_token,
            )
        async with self._state_lock:
            if self._active_task is task:
                self._active_run_id = None
                self._active_task = None
                self._active_fencing_token = None
        return True

    async def suspend_for_restart(self, run_id: str) -> bool:
        """Stop an app-owned task while preserving its resumable checkpoint."""
        async with self._state_lock:
            if self._active_run_id != run_id or self._active_task is None:
                return False
            task = self._active_task
            self._suspend_on_cancel.add(run_id)
            task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        async with self._state_lock:
            if self._active_task is task:
                self._active_run_id = None
                self._active_task = None
                self._active_fencing_token = None
            self._suspend_on_cancel.discard(run_id)
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
                live_event: SessionEvent | None
                try:
                    live_event = await asyncio.wait_for(
                        queue.get(), timeout=self._poll_interval_seconds
                    )
                except TimeoutError:
                    target = await asyncio.to_thread(self.journal.latest_sequence)
                    if target <= cursor:
                        continue
                    live_event = None
                else:
                    if live_event.sequence <= cursor:
                        continue
                    target = live_event.sequence
                if target == cursor + 1 and live_event is not None:
                    cursor = live_event.sequence
                    yield live_event
                    continue
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
        finally:
            self._subscribers.discard(queue)

    async def recover_interrupted_runs(self) -> tuple[str, ...]:
        """Close abandoned starts as retryable interruptions without resuming them."""
        recovered: list[str] = []
        async with self._publish_lock:
            lease_event = await asyncio.to_thread(
                self.journal.recover_run_lease,
                recovery_owner_id=self.owner_id,
            )
            if lease_event is not None:
                self._broadcast(lease_event)
                recovered.append(lease_event.run_id)
        run_ids = await asyncio.to_thread(self.journal.unfinished_run_ids)
        for run_id in run_ids:
            recoverable_approval = await asyncio.to_thread(
                self.journal.recoverable_approval, run_id
            )
            if recoverable_approval is not None:
                continue
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
        fencing_token: int,
        event_stream: AsyncIterator[RunEvent | Mapping[str, Any]],
    ) -> None:
        terminal_seen = False
        try:
            async for raw_event in event_stream:
                event = _coerce_event(raw_event)
                if terminal_seen:
                    break
                await asyncio.shield(
                    self._persist(
                        run_id=run_id, event=event, fencing_token=fencing_token
                    )
                )
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
                    fencing_token=fencing_token,
                )
        except asyncio.CancelledError:
            cleanup_task: asyncio.Task[Any]
            if run_id in self._suspend_on_cancel:
                cleanup_task = asyncio.create_task(
                    asyncio.to_thread(
                        self.journal.release_run_lease,
                        run_id,
                        owner_id=self.owner_id,
                        fencing_token=fencing_token,
                    )
                )
            else:
                cleanup_task = asyncio.create_task(
                    self._persist(
                        run_id=run_id,
                        event=RunEvent(
                            kind="terminal",
                            status="cancelled",
                            payload={"event": "run_cancelled"},
                        ),
                        fencing_token=fencing_token,
                    )
                )
            await _wait_through_cancellation(cleanup_task)
            raise
        except Exception as exc:
            await self._persist(
                run_id=run_id,
                event=RunEvent(
                    kind="terminal",
                    status="error",
                    payload={"event": "run_error", "error_type": type(exc).__name__},
                ),
                fencing_token=fencing_token,
            )
            raise
        finally:
            with suppress(Exception):
                await _close_unowned_stream(event_stream)
            async with self._state_lock:
                if self._active_run_id == run_id:
                    self._active_run_id = None
                    self._active_task = None
                    self._active_fencing_token = None
                self._suspend_on_cancel.discard(run_id)

    async def _persist(
        self, *, run_id: str, event: RunEvent, fencing_token: int | None = None
    ) -> SessionEvent:
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
                    lease_owner_id=self.owner_id if fencing_token is not None else None,
                    fencing_token=fencing_token,
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
                    lease_owner_id=self.owner_id if fencing_token is not None else None,
                    fencing_token=fencing_token,
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


async def _wait_through_cancellation(
    task: (
        asyncio.Task[BeginRunResult]
        | asyncio.Task[SessionEvent]
        | asyncio.Task[bool]
        | asyncio.Task[int]
        | asyncio.Task[None]
    ),
) -> None:
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
