# pgvector Retrieval and Indexing

> Approved extractive Markdown snapshot for Sage RAG evaluation.
> Upstream: `pgvector/pgvector`, `README.md`.
> The manifest pins the exact commit, raw-file hash, retrieval time, and PostgreSQL license.

## Exact search is the default

pgvector performs exact nearest-neighbor search by default, which gives perfect recall relative to
that distance calculation. Approximate indexes are optional. Exact search can be queried by
ordering on a distance operator and applying `LIMIT`.

## Approximate indexes and trade-offs

HNSW and IVFFlat trade some recall for speed. Adding an approximate index can change returned
neighbors. HNSW has a strong speed-recall trade-off but generally builds more slowly and uses more
memory than IVFFlat; IVFFlat requires a training step and suitable list/probe settings.

## HNSW controls

HNSW uses a multilayer graph. `m` and `ef_construction` affect index construction, while
`hnsw.ef_search` controls the query candidate list. Raising `ef_search` can improve recall at the
cost of speed. `SET LOCAL` can scope the setting to one transaction.

## Filtering and iterative scans

With approximate indexes, filtering is applied after the index scan and can produce fewer matching
rows than requested. pgvector supports iterative scans that can continue scanning until enough
rows are found or a configured limit is reached. Exact indexes on selective filter columns,
partial vector indexes, or partitioning may be better for some workloads.

## Hybrid search

The official README shows vector search used with PostgreSQL full text search. Full text candidates
can be ranked with `ts_rank_cd`, while vector candidates use a distance operator. Reciprocal Rank
Fusion or a cross-encoder can combine the two ranked lists.

## Measuring recall

Approximate recall should be monitored by comparing approximate results with exact search. One
documented method disables index scans inside a transaction to obtain an exact reference result.
The index choice should therefore be driven by measured recall and latency, not by the presence of
the HNSW feature alone.
