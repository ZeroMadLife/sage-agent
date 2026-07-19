"""Application-owned coding run coordinators."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from core.coding.memory import workspace_id_from_path
from core.coding.persistence import CodingSessionStore
from core.coding.persistence.session_event_journal import SessionEvent, SessionEventJournal
from core.coding.run_coordinator import RunCoordinator, RunEvent

if TYPE_CHECKING:
    from core.harness.capability_health_store import CapabilityHealthStore


class CodingRunRegistry:
    """Own one durable coordinator per coding session for an app instance."""

    def __init__(
        self,
        storage_root: Path,
        *,
        capability_health_store: CapabilityHealthStore | None = None,
    ) -> None:
        self.storage_root = storage_root
        self.owner_id = f"app-{uuid4().hex}"
        self.owner_pid = os.getpid()
        self._coordinators: dict[str, RunCoordinator] = {}
        self._hydrated: set[str] = set()
        self._lock = asyncio.Lock()
        self._capability_health_store = capability_health_store

    def get(self, session_id: str) -> RunCoordinator:
        coordinator = self._coordinators.get(session_id)
        if coordinator is None:
            coordinator = RunCoordinator(
                SessionEventJournal(self.storage_root, session_id),
                owner_id=self.owner_id,
                owner_pid=self.owner_pid,
                event_observer=self._observe_event,
            )
            self._coordinators[session_id] = coordinator
        return coordinator

    async def _observe_event(self, event: SessionEvent) -> None:
        if (
            self._capability_health_store is None
            or event.payload.get("type") != "capability_invocation_completed"
        ):
            return
        await asyncio.to_thread(self._record_capability_event, event)

    def _record_capability_event(self, event: SessionEvent) -> None:
        if self._capability_health_store is None:
            return
        session = CodingSessionStore(self.storage_root / "sessions").load(event.session_id)
        workspace_root = Path(str(session["workspace_root"])).resolve()
        self._capability_health_store.record_event(
            RunEvent(
                kind=event.kind,
                status=event.status,
                payload=event.payload,
                event_id=event.event_id,
                timestamp=event.timestamp,
            ),
            owner_id=str(session.get("owner_user_id") or "local"),
            workspace_id=workspace_id_from_path(workspace_root),
            session_id=event.session_id,
            run_id=event.run_id,
        )

    async def hydrate(self, session_id: str) -> RunCoordinator:
        """Recover prior-process leases at most once for this app instance."""
        async with self._lock:
            coordinator = self.get(session_id)
            if session_id not in self._hydrated:
                await coordinator.recover_interrupted_runs()
                self._hydrated.add(session_id)
            return coordinator

    async def shutdown(self) -> None:
        """Stop app-owned tasks while preserving checkpoint-backed approvals."""
        active = [
            (coordinator, coordinator.active_run_id)
            for coordinator in self._coordinators.values()
            if coordinator.active_run_id is not None
        ]
        await asyncio.gather(
            *(
                self._shutdown_run(coordinator, run_id)
                for coordinator, run_id in active
                if run_id is not None
            ),
            return_exceptions=True,
        )

    async def _shutdown_run(self, coordinator: RunCoordinator, run_id: str) -> bool:
        pending = await asyncio.to_thread(
            coordinator.journal.recoverable_approval,
            run_id,
        )
        if pending is not None:
            return await coordinator.suspend_for_restart(run_id)
        return await coordinator.cancel(run_id)
