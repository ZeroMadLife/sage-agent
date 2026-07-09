"""Background worker manager for coding subagents."""

from __future__ import annotations

import asyncio
import queue
import threading
from collections.abc import Callable
from typing import Any

from core.coding.context import WorkspaceContext, now
from core.coding.multiagent.execution import WorkerTask, clean_scope, clean_type
from core.coding.multiagent.runtime import run_worker_task


class WorkerManager:
    """Manage worker lifecycle and notifications."""

    def __init__(self, workspace: WorkspaceContext, model_factory: Callable[[], Any]) -> None:
        self.workspace = workspace
        self.model_factory = model_factory
        self._next_id = 1
        self._tasks: dict[str, WorkerTask] = {}
        self._notifications: queue.Queue[str] = queue.Queue()

    def spawn(
        self,
        description: str,
        prompt: str,
        subagent_type: str = "worker",
        write_scope: list[str] | str | None = None,
    ) -> dict[str, str]:
        """Spawn a background worker."""
        subagent_type = clean_type(subagent_type)
        scope = tuple(clean_scope(write_scope))
        task_id = f"agent_{self._next_id}"
        self._next_id += 1
        task = WorkerTask(
            id=task_id,
            description=description.strip() or "Worker task",
            subagent_type=subagent_type,
            write_scope=scope,
            prompt=prompt,
            status="running",
        )
        self._tasks[task_id] = task
        thread = threading.Thread(target=self._run_worker, args=(task,), daemon=True)
        task.thread = thread
        thread.start()
        return {"task_id": task.id, "status": "started", "description": task.description}

    def send_message(self, task_id: str, message: str) -> dict[str, str]:
        """Continue a worker with a new message."""
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"unknown worker: {task_id}")
        return self.spawn(task.description, message, task.subagent_type, list(task.write_scope))

    def stop(self, task_id: str) -> dict[str, str]:
        """Mark a task as stopping."""
        task = self._tasks.get(task_id)
        if task is None:
            raise ValueError(f"unknown worker: {task_id}")
        task.status = "stopping"
        task.updated_at = now()
        return {"task_id": task.id, "status": task.status, "description": task.description}

    def wait(self, task_id: str, timeout: float = 5) -> None:
        """Wait for one worker thread."""
        task = self._tasks[task_id]
        if task.thread is not None:
            task.thread.join(timeout)

    def drain_notifications(self) -> list[str]:
        """Drain completed worker notifications."""
        notifications: list[str] = []
        while True:
            try:
                notifications.append(self._notifications.get_nowait())
            except queue.Empty:
                return notifications

    def _run_worker(self, task: WorkerTask) -> None:
        try:
            task.result = asyncio.run(run_worker_task(task, self.workspace, self.model_factory))
            task.status = "completed"
        except Exception as exc:
            task.result = str(exc)
            task.status = "error"
        task.updated_at = now()
        self._notifications.put(f"{task.id} finished: {task.result}")
