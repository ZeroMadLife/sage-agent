"""Checkpointer construction and thread-scoped graph configuration."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from sage_harness.config import HarnessRunContext


class CheckpointScopeError(RuntimeError):
    """A durable checkpoint is not owned by the current server-side scope."""


def thread_config(thread_id: str, *, recursion_limit: int = 100) -> dict[str, Any]:
    """Build the only graph config surface owned by the harness runtime."""
    normalized = str(thread_id).strip()
    if not normalized:
        raise ValueError("thread_id must not be empty")
    if recursion_limit < 1:
        raise ValueError("recursion_limit must be positive")
    return {
        "configurable": {"thread_id": normalized},
        "recursion_limit": recursion_limit,
    }


def build_memory_checkpointer() -> BaseCheckpointSaver[Any]:
    """Return a process-local saver for tests and development smoke runs."""
    return InMemorySaver()


async def load_scoped_checkpoint(
    checkpointer: BaseCheckpointSaver[Any],
    context: HarnessRunContext,
) -> Any | None:
    """Load a checkpoint only after validating its durable thread binding."""
    try:
        checkpoint_tuple = await checkpointer.aget_tuple(
            cast(RunnableConfig, thread_config(context.thread_id))
        )
    except Exception as exc:
        raise CheckpointScopeError("checkpoint scope could not be read") from exc
    if checkpoint_tuple is None:
        return None

    checkpoint = getattr(checkpoint_tuple, "checkpoint", None)
    channels = checkpoint.get("channel_values") if isinstance(checkpoint, Mapping) else None
    thread_data = channels.get("thread_data") if isinstance(channels, Mapping) else None
    if not isinstance(thread_data, Mapping):
        raise CheckpointScopeError("checkpoint scope is missing its durable binding")

    expected: dict[str, str] = {
        "owner_id": context.owner_id,
        "workspace_id": context.workspace_id,
        "thread_id": context.thread_id,
        "workspace_path": context.workspace_path,
    }
    stored_path = thread_data.get("workspace_path")
    if not isinstance(stored_path, str) or stored_path != expected["workspace_path"]:
        raise CheckpointScopeError("checkpoint scope does not match current run")
    for field_name in ("owner_id", "workspace_id", "thread_id"):
        stored = thread_data.get(field_name)
        if stored is not None and stored != expected[field_name]:
            raise CheckpointScopeError("checkpoint scope does not match current run")
    return checkpoint_tuple


@asynccontextmanager
async def open_sqlite_checkpointer(
    path: Path | str,
) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    """Open a durable async SQLite checkpointer for one application lifespan."""
    database_path = Path(path).expanduser()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    except ImportError as exc:  # pragma: no cover - exercised by packaging smoke
        raise RuntimeError(
            "SQLite checkpointer support requires langgraph-checkpoint-sqlite"
        ) from exc

    async with AsyncSqliteSaver.from_conn_string(str(database_path)) as saver:
        await saver.setup()
        yield saver


__all__ = [
    "CheckpointScopeError",
    "build_memory_checkpointer",
    "load_scoped_checkpoint",
    "open_sqlite_checkpointer",
    "thread_config",
]
