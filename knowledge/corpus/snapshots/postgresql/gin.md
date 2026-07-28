# PostgreSQL GIN Indexes

> Approved extractive Markdown snapshot for Sage RAG evaluation.
> Upstream: `postgres/postgres`, `doc/src/sgml/gin.sgml`.
> The manifest pins the exact commit, raw-file hash, retrieval time, and PostgreSQL license.

## What GIN indexes

GIN means Generalized Inverted Index. It is designed for composite values where queries search
for element values contained in an item. A GIN index stores keys extracted from indexed values and
posting information that identifies rows containing those keys.

## GIN and full text search

For full text search, lexemes from a `tsvector` are the indexed keys. GIN accelerates candidate
matching for the `@@` operator. It does not by itself define a relevance score, so ranking still
uses functions such as `ts_rank` or `ts_rank_cd` after matching.

## Operator class boundary

GIN behavior is provided through an operator class that defines how keys are extracted from
indexed values and queries and how consistency is checked. This is why GIN can support different
composite data types rather than being a text-search-only index.

## Update trade-off

GIN can buffer insertions through its pending list when `fastupdate` is enabled. This improves
update throughput in many workloads but moves some work to pending-list cleanup. Index write cost
and query latency therefore need workload-specific measurement.
