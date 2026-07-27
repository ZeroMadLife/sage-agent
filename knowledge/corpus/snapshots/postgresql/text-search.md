# PostgreSQL Full Text Search Controls

> Approved extractive Markdown snapshot for Sage RAG evaluation.
> Upstream: `postgres/postgres`, `doc/src/sgml/textsearch.sgml`.
> The manifest pins the exact commit, raw-file hash, retrieval time, and PostgreSQL license.

## Documents and queries

PostgreSQL converts source text to `tsvector` and user input to `tsquery`. `to_tsvector` parses a
document into normalized lexemes with positions. Query constructors such as `plainto_tsquery`,
`phraseto_tsquery`, and `websearch_to_tsquery` provide different parsing semantics.

## Ranking with ts_rank and ts_rank_cd

PostgreSQL provides `ts_rank` and `ts_rank_cd` as built-in relevance-ranking functions.
`ts_rank_cd` considers cover density, while both functions can apply normalization flags. These
scores rank matching documents; they are not BM25 scores and are not calibrated probabilities.

## Normalization is not probability

Ranking does not use global corpus information. Normalization flag 32 maps a score through
`rank / (rank + 1)`, but this only rescales the value and does not change ordering or make it a
probability. Applications may combine built-in rank with other business signals.

## Stored search vector and GIN

A table can store a generated `tsvector` column and create a GIN index on it. A query first uses
the match operator `@@` to select documents and then orders matches with `ts_rank` or
`ts_rank_cd`. Indexing and ranking are separate responsibilities.
