"""Checkpointer 构造与 thread 作用域 Graph 配置。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from sage_harness.config import HarnessRunContext
from sage_harness.state import normalize_turn_context_plan_binding


class CheckpointScopeError(RuntimeError):
    """持久化 Checkpoint 不属于当前服务端作用域。"""


def thread_config(thread_id: str, *, recursion_limit: int = 100) -> dict[str, Any]:
    """构造 Harness Runtime 唯一拥有的 Graph 配置入口。"""
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
    """返回供测试和开发 smoke run 使用的进程内 Checkpointer。"""
    return InMemorySaver()


async def load_scoped_checkpoint(
    checkpointer: BaseCheckpointSaver[Any],
    context: HarnessRunContext,
    *,
    expected_plan_binding: Mapping[str, object] | None = None,
) -> Any | None:
    """校验 thread scope；恢复时还必须精确匹配不可变 Plan binding。"""
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
    if not isinstance(channels, Mapping):
        raise CheckpointScopeError("checkpoint scope is missing its durable binding")
    thread_data = channels.get("thread_data")
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
        # 历史 Checkpoint 仍可走非 enforce 兼容路径；Plan 恢复必须具备完整作用域。
        if expected_plan_binding is not None and not isinstance(stored, str):
            raise CheckpointScopeError("checkpoint scope is incomplete for plan resume")
        if stored is not None and stored != expected[field_name]:
            raise CheckpointScopeError("checkpoint scope does not match current run")
    if expected_plan_binding is not None:
        try:
            expected_binding = normalize_turn_context_plan_binding(expected_plan_binding)
            stored_binding = channels.get("turn_context_plan")
            if not isinstance(stored_binding, Mapping):
                raise ValueError("missing plan binding")
            normalized_stored = normalize_turn_context_plan_binding(stored_binding)
        except (TypeError, ValueError) as exc:
            raise CheckpointScopeError("checkpoint plan binding is missing or invalid") from exc
        if normalized_stored != expected_binding:
            raise CheckpointScopeError("checkpoint plan binding does not match current run")
    return checkpoint_tuple


@asynccontextmanager
async def open_sqlite_checkpointer(
    path: Path | str,
) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    """为一次应用生命周期打开持久化异步 SQLite Checkpointer。"""
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
