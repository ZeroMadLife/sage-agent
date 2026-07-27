# LangGraph Persistence

> Approved extractive Markdown snapshot for Sage RAG evaluation.
> Upstream: `langchain-ai/docs`, `src/oss/langgraph/persistence.mdx`.
> The manifest pins the exact commit, raw-file hash, retrieval time, and MIT license.

## Checkpoints and threads

Compiling a graph with a checkpointer saves graph state as checkpoints at execution-step
boundaries. Checkpoints are organized into threads. A `thread_id` is supplied in the
`configurable` section so the checkpointer can load state and resume the correct execution.

## Why persistence

Persistence supports human-in-the-loop workflows, conversational memory, time-travel debugging,
and fault recovery. A checkpoint is a snapshot of graph state at a super-step boundary; it is not
the same thing as an arbitrary application log line.

## Pending writes

When one node fails during a super-step, writes from nodes that already completed can be stored as
pending writes. On resume, those successful nodes do not need to run again. Pending writes are
task-level writes, not full `StateSnapshot` checkpoints used for time travel.

## Checkpointer interface

Checkpointers conform to `BaseCheckpointSaver`. The core synchronous operations include `put`,
`put_writes`, `get_tuple`, and `list`; asynchronous graph execution uses their async equivalents.
The application chooses a checkpointer implementation appropriate for its storage requirements.
