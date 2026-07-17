"""Application-owned coding run coordinators."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

from core.coding.persistence.session_event_journal import SessionEventJournal
from core.coding.run_coordinator import RunCoordinator


class CodingRunRegistry:
    """Own one durable coordinator per coding session for an app instance."""

    def __init__(self, storage_root: Path) -> None:
        self.storage_root = storage_root
        self.owner_id = f"app-{uuid4().hex}"
        self.owner_pid = os.getpid()
        self._coordinators: dict[str, RunCoordinator] = {}
        self._hydrated: set[str] = set()
        self._lock = asyncio.Lock()

    def get(self, session_id: str) -> RunCoordinator:
        coordinator = self._coordinators.get(session_id)
        if coordinator is None:
            coordinator = RunCoordinator(
                SessionEventJournal(self.storage_root, session_id),
                owner_id=self.owner_id,
                owner_pid=self.owner_pid,
            )
            self._coordinators[session_id] = coordinator
        return coordinator

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
