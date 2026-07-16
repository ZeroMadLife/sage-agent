"""Checkpointer construction and thread-scoped graph configuration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver


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


__all__ = ["build_memory_checkpointer", "open_sqlite_checkpointer", "thread_config"]
