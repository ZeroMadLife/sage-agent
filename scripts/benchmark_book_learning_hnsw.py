"""Benchmark exact pgvector against an ephemeral HNSW index on real long books."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import tempfile
import time
import uuid
from dataclasses import asdict
from importlib import import_module
from pathlib import Path
from typing import Any

from core.config.settings import get_settings
from core.knowledge.benchmark import evaluate_retrieval_v2, load_benchmark_v2, passage_id
from core.knowledge.benchmark_runner import (
    build_benchmark_store,
    cleanup_benchmark_store,
    load_embedding_provider,
    load_manifest,
)
from core.knowledge.postgres_index import PostgresKnowledgeIndex, PostgresKnowledgeIndexConfig
from core.knowledge.postgres_retrieval import PgvectorExactRetriever, PgvectorHnswRetriever
from core.knowledge.retrieval import KnowledgeAblationPolicy
from evals.book_learning_hnsw import (
    BookHnswConfig,
    BookHnswMeasurement,
    choose_hnsw_storage,
    decide_book_hnsw_gate,
)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=repo_root / "evals" / "book_learning_benchmark_v1_manifest.json",
    )
    parser.add_argument(
        "--postgres-dsn",
        default=settings.knowledge_postgres_dsn or settings.postgres_sync_dsn,
        help="Defaults to Knowledge/PostgreSQL settings and is never written to reports.",
    )
    parser.add_argument(
        "--provider-factory",
        default="scripts.benchmark_providers.doubao_multimodal:create_provider",
    )
    parser.add_argument("--strategy", default="contextual_chunk")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--warmup-passes", type=int, default=1)
    parser.add_argument("--measured-passes", type=int, default=2)
    parser.add_argument("--ef-search", default="40,80,120,200")
    parser.add_argument("--exact-p95-sla-ms", type=float, default=100.0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--confirm-ephemeral-write", action="store_true")
    args = parser.parse_args()
    if not args.skip_fetch:
        parser.error("--skip-fetch is required after corpus checksum verification")
    if not args.confirm_ephemeral_write:
        parser.error("--confirm-ephemeral-write is required")
    source = _source_state(repo_root)
    if source["dirty"] and not args.allow_dirty:
        parser.error("book HNSW evidence requires a clean source tree")
    if args.warmup_passes < 0 or args.measured_passes < 1:
        parser.error("warmup must be non-negative and measured passes must be positive")

    manifest = load_manifest(repo_root, args.manifest.resolve())
    queries = load_benchmark_v2(repo_root / manifest.dataset)
    provider = load_embedding_provider(args.provider_factory)
    policy = KnowledgeAblationPolicy(strategy=args.strategy)
    storage = choose_hnsw_storage(provider.dimensions)
    config = BookHnswConfig(
        exact_p95_sla_ms=args.exact_p95_sla_ms,
        ef_search_values=_integer_tuple(args.ef_search),
    )
    workspace_id = f"sage-book-hnsw-{uuid.uuid4().hex}"
    index = PostgresKnowledgeIndex(
        PostgresKnowledgeIndexConfig(dsn=args.postgres_dsn),
        workspace_id=workspace_id,
        embedding_provider=provider,
        ablation_policy=policy,
        dense_retriever=PgvectorExactRetriever(),
    )
    index_name = f"knowledge_book_hnsw_{uuid.uuid4().hex[:12]}"
    store = None
    index_created = False
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="sage-book-hnsw-", dir=repo_root / ".coding") as temp:
            temporary = Path(temp)
            store, chunking, _available = build_benchmark_store(
                repo_root,
                manifest,
                workspace_path=temporary / "workspace",
                database_path=temporary / "knowledge.sqlite3",
                embedding_provider=provider,
                ablation_policy=policy,
                query_texts=tuple(item.query for item in queries),
                index_factory=lambda _workspace, _provider, _policy: index,
                workspace_id=workspace_id,
            )
            exact = _measure(
                store,
                queries,
                top_k=args.top_k,
                warmup_passes=args.warmup_passes,
                measured_passes=args.measured_passes,
            )
            hnsw_build = _create_hnsw_index(
                index,
                index_name=index_name,
                storage=storage,
                m=config.hnsw_m,
                ef_construction=config.hnsw_ef_construction,
            )
            index_created = True
            hnsw_runs: list[dict[str, object]] = []
            decision_measurements: list[BookHnswMeasurement] = []
            for ef_search in config.ef_search_values:
                index.dense_retriever = PgvectorHnswRetriever(
                    dimensions=provider.dimensions,
                    ef_search=ef_search,
                )
                measured = _measure(
                    store,
                    queries,
                    top_k=args.top_k,
                    warmup_passes=args.warmup_passes,
                    measured_passes=args.measured_passes,
                )
                oracle_recall = _oracle_recall(exact["chunk_ids"], measured["chunk_ids"])
                measurement = BookHnswMeasurement(
                    ef_search=ef_search,
                    recall_at_k=oracle_recall,
                    p50_ms=float(measured["latency_ms"]["p50"]),
                    p95_ms=float(measured["latency_ms"]["p95"]),
                    measured_queries=len(queries) * args.measured_passes,
                )
                decision_measurements.append(measurement)
                hnsw_runs.append(
                    {
                        **asdict(measurement),
                        "gold_retrieval": measured["gold_retrieval"],
                        "plan": _explain(index, queries[0].query, top_k=args.top_k),
                    }
                )
            decision = decide_book_hnsw_gate(
                config,
                exact_p95_ms=float(exact["latency_ms"]["p95"]),
                measurements=tuple(decision_measurements),
            )
            result = {
                "schema_version": 1,
                "benchmark_id": "sage-book-learning-hnsw-v1",
                "source": source,
                "benchmark_revision": manifest.benchmark_revision,
                "provider": {
                    "model_id": provider.model_id,
                    "model_revision": provider.model_revision,
                    "dimensions": provider.dimensions,
                },
                "corpus": {
                    "real_public_domain_books": True,
                    "file_count": len(manifest.files),
                    "chunking": chunking,
                    "indexed_chunk_count": chunking["planned_chunk_count"],
                    "gold_status": "seed_manual",
                },
                "config": {
                    **asdict(config),
                    "top_k": args.top_k,
                    "warmup_passes": args.warmup_passes,
                    "measured_passes": args.measured_passes,
                    "distance": "cosine",
                },
                "storage": asdict(storage),
                "exact": {
                    "latency_ms": exact["latency_ms"],
                    "measured_queries": len(queries) * args.measured_passes,
                    "gold_retrieval": exact["gold_retrieval"],
                },
                "hnsw_build": hnsw_build,
                "hnsw": hnsw_runs,
                "decision": asdict(decision),
                "benchmark_wall_ms": round((time.perf_counter() - started) * 1_000, 3),
                "evidence_boundaries": {
                    "runtime_hnsw_enabled": False,
                    "ephemeral_partial_index": True,
                    "halfvec_cast_is_lossy": storage.lossy_cast,
                    "query_embedding_latency_included": True,
                    "production_traffic": False,
                    "production_sla_claim": False,
                },
            }
    finally:
        if index_created:
            _drop_hnsw_index(index, index_name)
        if store is not None:
            cleanup_benchmark_store(store)
        else:
            index.close()
    result["ephemeral_cleanup_completed"] = _index_absent(args.postgres_dsn, index_name)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def _measure(
    store: Any,
    queries: tuple[Any, ...],
    *,
    top_k: int,
    warmup_passes: int,
    measured_passes: int,
) -> dict[str, object]:
    for _ in range(warmup_passes):
        for query in queries:
            store.search(query.query, top_k=top_k, retrieval_mode="dense")
    latencies: list[float] = []
    ranked: dict[str, tuple[str, ...]] = {}
    chunk_ids: dict[str, tuple[str, ...]] = {}
    for _ in range(measured_passes):
        for query in queries:
            started = time.perf_counter()
            hits = store.search(query.query, top_k=top_k, retrieval_mode="dense")
            latencies.append((time.perf_counter() - started) * 1_000)
            ranked[query.query_id] = tuple(
                dict.fromkeys(
                    passage_id(
                        hit.chunk.source_relative_path,
                        _section(
                            hit.chunk.source_relative_path,
                            hit.chunk.heading_path or (hit.chunk.title,),
                        ),
                    )
                    for hit in hits
                )
            )
            chunk_ids[query.query_id] = tuple(hit.chunk.chunk_id for hit in hits)
    return {
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "gold_retrieval": asdict(evaluate_retrieval_v2(queries, ranked, top_k=top_k)),
        "chunk_ids": chunk_ids,
    }


def _create_hnsw_index(
    index: PostgresKnowledgeIndex,
    *,
    index_name: str,
    storage: Any,
    m: int,
    ef_construction: int,
) -> dict[str, object]:
    sql = import_module("psycopg2.sql")
    storage_type = sql.SQL(f"{storage.sql_type}({storage.dimensions})")
    started = time.perf_counter()
    with index._connection() as postgres, postgres.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "CREATE INDEX {} ON knowledge_index_chunks USING hnsw "
                "((embedding::{}) {}) WITH (m = {}, ef_construction = {}) "
                "WHERE workspace_id = {} AND visibility = 'private' AND active "
                "AND embedding_model = {} AND embedding_revision = {} "
                "AND embedding_dimensions = {}"
            ).format(
                sql.Identifier(index_name),
                storage_type,
                sql.SQL(storage.operator_class),
                sql.Literal(m),
                sql.Literal(ef_construction),
                sql.Literal(index.workspace_id),
                sql.Literal(index.embedding_provider.model_id),
                sql.Literal(index.embedding_provider.model_revision),
                sql.Literal(storage.dimensions),
            )
        )
        cursor.execute("ANALYZE knowledge_index_chunks")
        cursor.execute("SELECT pg_relation_size(%s::regclass)", (index_name,))
        index_bytes = int(cursor.fetchone()[0])
    return {
        "ran": True,
        "m": m,
        "ef_construction": ef_construction,
        "build_ms": round((time.perf_counter() - started) * 1_000, 3),
        "index_bytes": index_bytes,
    }


def _drop_hnsw_index(index: PostgresKnowledgeIndex, index_name: str) -> None:
    sql = import_module("psycopg2.sql")
    with index._connection() as postgres, postgres.cursor() as cursor:
        cursor.execute(sql.SQL("DROP INDEX IF EXISTS {}").format(sql.Identifier(index_name)))


def _explain(index: PostgresKnowledgeIndex, query: str, *, top_k: int) -> dict[str, object]:
    provider = index.embedding_provider
    vector = index._database_vector(tuple(float(value) for value in provider.embed_query(query)))
    dimensions = provider.dimensions
    expression = (
        "embedding" if dimensions <= 2_000 else f"embedding::halfvec({dimensions})"
    )
    with index._connection() as postgres, postgres.cursor() as cursor:
        cursor.execute("SET LOCAL enable_seqscan = off")
        cursor.execute(
            f"""
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT chunk_id FROM knowledge_index_chunks
            WHERE workspace_id=%s AND visibility='private' AND active
              AND embedding_model=%s AND embedding_revision=%s
              AND embedding_dimensions=%s
            ORDER BY {expression} <=> ((%s::vector)::halfvec({dimensions}))
            LIMIT %s
            """,
            (
                index.workspace_id,
                provider.model_id,
                provider.model_revision,
                dimensions,
                vector,
                max(20, top_k * 5),
            ),
        )
        raw = cursor.fetchone()[0]
    payload = raw[0]
    return {
        "execution_time_ms": float(payload.get("Execution Time", 0.0)),
        "plan_node_types": _plan_nodes(payload["Plan"]),
        "index_name": _plan_index_name(payload["Plan"]),
    }


def _plan_nodes(plan: dict[str, Any]) -> list[str]:
    result = [str(plan.get("Node Type", "unknown"))]
    for child in plan.get("Plans", []):
        result.extend(_plan_nodes(child))
    return result


def _plan_index_name(plan: dict[str, Any]) -> str | None:
    if plan.get("Index Name"):
        return str(plan["Index Name"])
    for child in plan.get("Plans", []):
        value = _plan_index_name(child)
        if value:
            return value
    return None


def _oracle_recall(
    exact: dict[str, tuple[str, ...]], candidate: dict[str, tuple[str, ...]]
) -> float:
    values = [
        len(set(candidate[query_id]).intersection(gold)) / len(gold)
        for query_id, gold in exact.items()
        if gold
    ]
    return round(sum(values) / len(values), 6)


def _section(source_path: str, heading_path: tuple[str, ...]) -> str:
    return " / ".join(heading_path) if Path(source_path).suffix.lower() == ".txt" else heading_path[-1]


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 3)
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 3)


def _integer_tuple(raw: str) -> tuple[int, ...]:
    values = tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    if not values:
        raise ValueError("ef_search cannot be empty")
    return values


def _source_state(repo_root: Path) -> dict[str, object]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root, check=True, capture_output=True, text=True
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {"commit": commit, "dirty": bool(status.strip())}


def _index_absent(dsn: str, index_name: str) -> bool:
    psycopg2 = import_module("psycopg2")
    with psycopg2.connect(dsn) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s) IS NULL", (index_name,))
        return bool(cursor.fetchone()[0])


if __name__ == "__main__":
    raise SystemExit(main())
