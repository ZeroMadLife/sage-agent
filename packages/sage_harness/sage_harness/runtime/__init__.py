"""Long-running graph runtime primitives for the Sage harness."""

from sage_harness.runtime.checkpoint import (
    CheckpointScopeError,
    build_memory_checkpointer,
    load_scoped_checkpoint,
    open_sqlite_checkpointer,
    thread_config,
)
from sage_harness.runtime.events import HarnessStreamItem, normalize_stream_item
from sage_harness.runtime.manager import HarnessRunManager, HarnessRunRequest
from sage_harness.runtime.message_compaction import (
    GraphMessageCompactionError,
    GraphMessageCompactionPlan,
    GraphMessageCompactionRequest,
    build_graph_message_compaction_plan,
    load_graph_message_compaction_plan,
)

__all__ = [
    "CheckpointScopeError",
    "GraphMessageCompactionError",
    "GraphMessageCompactionPlan",
    "GraphMessageCompactionRequest",
    "HarnessRunManager",
    "HarnessRunRequest",
    "HarnessStreamItem",
    "build_graph_message_compaction_plan",
    "build_memory_checkpointer",
    "load_graph_message_compaction_plan",
    "load_scoped_checkpoint",
    "normalize_stream_item",
    "open_sqlite_checkpointer",
    "thread_config",
]
