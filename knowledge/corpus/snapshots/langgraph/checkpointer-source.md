# LangGraph BaseCheckpointSaver Source Contract

> Approved extractive source snapshot for Sage RAG evaluation.
> Upstream: `langchain-ai/langgraph`, `libs/checkpoint/langgraph/checkpoint/base/__init__.py`.
> The manifest pins the exact commit, raw-file hash, retrieval time, and MIT license.

## BaseCheckpointSaver contract

`BaseCheckpointSaver` is the generic base class for checkpoint persistence. Concrete saver
implementations inherit this contract and provide storage-specific behavior. The base also exposes
serializer and channel-version responsibilities used by graph execution.

## Synchronous operations

The source contract includes `get_tuple` to fetch a checkpoint tuple, `list` to iterate matching
checkpoints, `put` to store a checkpoint with metadata and versions, and `put_writes` to persist
intermediate task writes linked to a checkpoint. The default base implementations raise
`NotImplementedError` where a storage backend must supply behavior.

## Asynchronous parity

Async graph execution uses `aget_tuple`, `alist`, `aput`, and `aput_writes`. The async methods
preserve the same responsibility split as their synchronous counterparts rather than silently
calling a blocking storage API.

## Intermediate writes

`put_writes` and `aput_writes` receive the related config, a sequence of channel/value writes, a
`task_id`, and an optional task path. They persist task progress separately from the full checkpoint
record, which is the source-level basis for pending-write recovery.
