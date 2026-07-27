"""Deterministic PostgreSQL exact/HNSW scale gate for the Knowledge index."""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass
from importlib import import_module
from typing import Any


@dataclass(frozen=True, slots=True)
class ScaleBenchmarkConfig:
    scales: tuple[int, ...] = (1_000, 10_000, 100_000)
    dimensions: int = 384
    top_k: int = 10
    query_count: int = 32
    warmup_passes: int = 1
    measured_passes: int = 2
    exact_p95_sla_ms: float = 100.0
    ef_search_values: tuple[int, ...] = (40, 80, 120, 200)
    hnsw_m: int = 16
    hnsw_ef_construction: int = 64
    minimum_hnsw_recall: float = 0.98
    minimum_hnsw_latency_reduction: float = 0.20

    def __post_init__(self) -> None:
        if not self.scales or self.scales != tuple(sorted(set(self.scales))):
            raise ValueError("scale values must be unique and strictly increasing")
        if self.scales[0] < self.query_count * self.top_k:
            raise ValueError("smallest scale cannot fit all gold neighbors")
        if not 32 <= self.dimensions <= 2_000:
            raise ValueError("dimensions must be between 32 and the pgvector HNSW limit 2000")
        if not 1 <= self.top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        if not 1 <= self.query_count <= 128:
            raise ValueError("query_count must be between 1 and 128")
        if self.dimensions < self.query_count + self.top_k + 16:
            raise ValueError(
                "dimensions cannot fit query, gold, and distractor reserved dimensions"
            )
        if not 0 <= self.warmup_passes <= 10:
            raise ValueError("warmup passes must be between 0 and 10")
        if not 1 <= self.measured_passes <= 20:
            raise ValueError("measured passes must be between 1 and 20")
        if not math.isfinite(self.exact_p95_sla_ms) or self.exact_p95_sla_ms <= 0:
            raise ValueError("exact P95 SLA must be a positive finite number")
        if not self.ef_search_values or self.ef_search_values != tuple(
            sorted(set(self.ef_search_values))
        ):
            raise ValueError("ef_search values must be unique and strictly increasing")
        if any(value < self.top_k or value > 10_000 for value in self.ef_search_values):
            raise ValueError("ef_search values must cover top_k and stay bounded")
        if not 2 <= self.hnsw_m <= 100:
            raise ValueError("HNSW m must be between 2 and 100")
        if not 4 <= self.hnsw_ef_construction <= 1_000:
            raise ValueError("HNSW ef_construction must be between 4 and 1000")
        if not 0.0 < self.minimum_hnsw_recall <= 1.0:
            raise ValueError("minimum HNSW recall must be in (0, 1]")
        if not 0.0 <= self.minimum_hnsw_latency_reduction < 1.0:
            raise ValueError("minimum HNSW latency reduction must be in [0, 1)")


DEFAULT_SCALE_BENCHMARK_CONFIG = ScaleBenchmarkConfig()


@dataclass(frozen=True, slots=True)
class ScaleMeasurement:
    scale: int
    recall_at_10: float
    p50_ms: float
    p95_ms: float
    measured_queries: int
    data_load_ms: float
    scalar_index_build_ms: float
    analyze_ms: float
    table_bytes: int
    total_index_bytes: int
    total_relation_bytes: int
    explain: dict[str, Any]
    backend_memory: dict[str, Any]


@dataclass(frozen=True, slots=True)
class HnswMeasurement:
    ef_search: int
    recall_at_10: float
    p50_ms: float
    p95_ms: float
    measured_queries: int
    explain: dict[str, Any]
    backend_memory: dict[str, Any]


@dataclass(frozen=True, slots=True)
class HnswGateDecision:
    triggered_scales: tuple[int, ...]
    hnsw_experiment_required: bool
    eligible_ef_search: tuple[int, ...]
    runtime_recommendation: str
    runtime_index_enabled: bool = False


def percentile(values: tuple[float, ...], quantile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be in [0, 1]")
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def query_vector(query_index: int, config: ScaleBenchmarkConfig) -> tuple[float, ...]:
    if not 0 <= query_index < config.query_count:
        raise ValueError("query index is out of range")
    values = [0.0] * config.dimensions
    values[query_index] = 1.0
    return tuple(values)


def positive_vector(
    query_index: int,
    rank: int,
    config: ScaleBenchmarkConfig,
) -> tuple[float, ...]:
    if not 0 <= rank < config.top_k:
        raise ValueError("gold rank is out of range")
    values = [0.0] * config.dimensions
    epsilon = (rank + 1) * 0.002
    values[query_index] = math.sqrt(1.0 - epsilon * epsilon)
    values[config.query_count + rank] = epsilon
    return tuple(values)


def distractor_vector(row_index: int, config: ScaleBenchmarkConfig) -> tuple[float, ...]:
    if row_index < 0:
        raise ValueError("distractor row index cannot be negative")
    reserved = config.query_count + config.top_k
    available = config.dimensions - reserved
    indexes: list[int] = []
    candidate = (
        int.from_bytes(hashlib.sha256(f"sage-scale-{row_index}".encode()).digest()[:8], "big")
        % available
    )
    step = 17
    while len(indexes) < min(8, available):
        absolute = reserved + candidate
        if absolute not in indexes:
            indexes.append(absolute)
        candidate = (candidate + step) % available
    magnitude = 1.0 / math.sqrt(len(indexes))
    values = [0.0] * config.dimensions
    for offset, index in enumerate(indexes):
        values[index] = magnitude if (row_index + offset) % 2 == 0 else -magnitude
    return tuple(values)


def gold_chunk_ids(query_index: int, config: ScaleBenchmarkConfig) -> tuple[str, ...]:
    return tuple(f"gold-q{query_index:03d}-r{rank:03d}" for rank in range(config.top_k))


def decide_hnsw_gate(
    *,
    config: ScaleBenchmarkConfig,
    exact: tuple[ScaleMeasurement, ...],
    hnsw: tuple[HnswMeasurement, ...],
) -> HnswGateDecision:
    triggered = tuple(
        measurement.scale for measurement in exact if measurement.p95_ms > config.exact_p95_sla_ms
    )
    if not triggered:
        return HnswGateDecision(
            triggered_scales=(),
            hnsw_experiment_required=False,
            eligible_ef_search=(),
            runtime_recommendation="keep_exact",
        )
    if not exact:
        raise ValueError("exact measurements are required for an HNSW decision")
    largest_exact_p95 = exact[-1].p95_ms
    maximum_hnsw_p95 = largest_exact_p95 * (1.0 - config.minimum_hnsw_latency_reduction)
    eligible = tuple(
        measurement.ef_search
        for measurement in hnsw
        if measurement.recall_at_10 >= config.minimum_hnsw_recall
        and measurement.p95_ms <= config.exact_p95_sla_ms
        and measurement.p95_ms <= maximum_hnsw_p95
    )
    return HnswGateDecision(
        triggered_scales=triggered,
        hnsw_experiment_required=True,
        eligible_ef_search=eligible,
        runtime_recommendation=(
            "hnsw_eligible_for_followup" if eligible else "keep_exact_hnsw_not_eligible"
        ),
    )


def run_scale_benchmark(
    dsn: str,
    *,
    config: ScaleBenchmarkConfig = DEFAULT_SCALE_BENCHMARK_CONFIG,
) -> dict[str, Any]:
    normalized_dsn = dsn.strip()
    if not normalized_dsn.startswith(("postgresql://", "postgres://")):
        raise ValueError("PostgreSQL scale benchmark requires a postgresql:// DSN")
    psycopg2 = import_module("psycopg2")
    sql = import_module("psycopg2.sql")
    register_vector = import_module("pgvector.psycopg2").register_vector
    numpy = import_module("numpy")
    table_name = f"sage_hnsw_scale_bench_{uuid.uuid4().hex[:12]}"
    scalar_index_name = f"{table_name}_scope_idx"
    hnsw_index_name = f"{table_name}_hnsw_idx"
    connection = psycopg2.connect(normalized_dsn, application_name="sage-hnsw-scale-gate")
    report: dict[str, Any] | None = None
    cleanup_completed = False
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(connection)
        environment = _postgres_environment(connection)
        exact_measurements: list[ScaleMeasurement] = []
        final_oracle: dict[int, tuple[str, ...]] = {}
        for scale in config.scales:
            _drop_benchmark_table(connection, sql, table_name)
            _create_benchmark_table(connection, sql, table_name, config.dimensions)
            load_ms = _load_fixture(connection, sql, table_name, scale, config)
            index_ms = _create_scalar_index(
                connection,
                sql,
                table_name,
                scalar_index_name,
            )
            analyze_ms = _analyze(connection, sql, table_name)
            measurement, oracle = _measure_exact(
                connection,
                sql,
                numpy,
                table_name,
                scalar_index_name,
                scale,
                config,
                data_load_ms=load_ms,
                scalar_index_build_ms=index_ms,
                analyze_ms=analyze_ms,
            )
            exact_measurements.append(measurement)
            final_oracle = oracle

        pre_hnsw_decision = decide_hnsw_gate(
            config=config,
            exact=tuple(exact_measurements),
            hnsw=(),
        )
        hnsw_measurements: list[HnswMeasurement] = []
        hnsw_build: dict[str, Any] = {
            "ran": False,
            "m": config.hnsw_m,
            "ef_construction": config.hnsw_ef_construction,
            "build_ms": None,
            "index_bytes": None,
        }
        if pre_hnsw_decision.hnsw_experiment_required:
            build_started = time.perf_counter()
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "CREATE INDEX {} ON {} USING hnsw "
                        "(embedding vector_cosine_ops) WITH (m = {}, ef_construction = {})"
                    ).format(
                        sql.Identifier(hnsw_index_name),
                        sql.Identifier(table_name),
                        sql.Literal(config.hnsw_m),
                        sql.Literal(config.hnsw_ef_construction),
                    )
                )
            hnsw_build_ms = (time.perf_counter() - build_started) * 1_000
            _analyze(connection, sql, table_name)
            hnsw_build = {
                "ran": True,
                "m": config.hnsw_m,
                "ef_construction": config.hnsw_ef_construction,
                "build_ms": hnsw_build_ms,
                "index_bytes": _relation_size(connection, hnsw_index_name),
            }
            for ef_search in config.ef_search_values:
                hnsw_measurements.append(
                    _measure_hnsw(
                        connection,
                        sql,
                        numpy,
                        table_name,
                        ef_search,
                        final_oracle,
                        config,
                    )
                )

        decision = decide_hnsw_gate(
            config=config,
            exact=tuple(exact_measurements),
            hnsw=tuple(hnsw_measurements),
        )
        deterministic_contract = {
            "config": _config_payload(config),
            "gold": {
                str(index): list(gold_chunk_ids(index, config))
                for index in range(config.query_count)
            },
        }
        report = {
            "schema_version": 1,
            "benchmark_id": "sage-postgres-hnsw-scale-gate-v1",
            "config": _config_payload(config),
            "environment": environment,
            "resource_measurement": {
                "server_cpu": {
                    "available": False,
                    "reason": (
                        "stock PostgreSQL exposes no portable per-query server CPU metric; "
                        "pg_stat_kcache and privileged container telemetry are not required"
                    ),
                },
                "server_execution": "EXPLAIN ANALYZE BUFFERS SETTINGS",
                "backend_memory": "pg_backend_memory_contexts when permitted",
            },
            "exact": [asdict(item) for item in exact_measurements],
            "hnsw_build": hnsw_build,
            "hnsw": [asdict(item) for item in hnsw_measurements],
            "decision": asdict(decision),
            "deterministic_digest": "sha256:"
            + hashlib.sha256(
                json.dumps(
                    deterministic_contract,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest(),
        }
    finally:
        try:
            _drop_benchmark_table(connection, sql, table_name)
            cleanup_completed = True
        finally:
            connection.close()
    if report is None:
        raise RuntimeError("scale benchmark did not produce a report")
    report["ephemeral_cleanup_completed"] = cleanup_completed
    return report


def _config_payload(config: ScaleBenchmarkConfig) -> dict[str, Any]:
    payload = asdict(config)
    payload["distance"] = "cosine"
    payload["operator"] = "<=>"
    return payload


def _create_benchmark_table(connection: Any, sql: Any, table_name: str, dimensions: int) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "CREATE UNLOGGED TABLE {} ("
                "chunk_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, "
                "visibility TEXT NOT NULL, active BOOLEAN NOT NULL, "
                "embedding_dimensions INTEGER NOT NULL, embedding vector({}) NOT NULL)"
            ).format(sql.Identifier(table_name), sql.Literal(dimensions))
        )


def _drop_benchmark_table(connection: Any, sql: Any, table_name: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(table_name)))


def _load_fixture(
    connection: Any,
    sql: Any,
    table_name: str,
    scale: int,
    config: ScaleBenchmarkConfig,
) -> float:
    started = time.perf_counter()
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8", newline="") as fixture:
        for query_index in range(config.query_count):
            for rank, chunk_id in enumerate(gold_chunk_ids(query_index, config)):
                fixture.write(
                    _copy_row(
                        chunk_id,
                        positive_vector(query_index, rank, config),
                        config.dimensions,
                    )
                )
        gold_count = config.query_count * config.top_k
        for row_index in range(scale - gold_count):
            fixture.write(
                _copy_row(
                    f"distractor-{row_index:09d}",
                    distractor_vector(row_index, config),
                    config.dimensions,
                )
            )
        fixture.seek(0)
        copy_statement = sql.SQL(
            "COPY {} (chunk_id, workspace_id, visibility, active, "
            "embedding_dimensions, embedding) FROM STDIN WITH (FORMAT text)"
        ).format(sql.Identifier(table_name))
        with connection.cursor() as cursor:
            cursor.copy_expert(copy_statement.as_string(connection), fixture)
    return (time.perf_counter() - started) * 1_000


def _copy_row(chunk_id: str, vector: tuple[float, ...], dimensions: int) -> str:
    return f"{chunk_id}\tscale-benchmark\tprivate\tt\t{dimensions}\t" f"{_vector_literal(vector)}\n"


def _vector_literal(vector: tuple[float, ...]) -> str:
    return "[" + ",".join(format(value, ".9g") for value in vector) + "]"


def _create_scalar_index(
    connection: Any,
    sql: Any,
    table_name: str,
    index_name: str,
) -> float:
    started = time.perf_counter()
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL(
                "CREATE INDEX {} ON {} " "(workspace_id, visibility, active, embedding_dimensions)"
            ).format(sql.Identifier(index_name), sql.Identifier(table_name))
        )
    return (time.perf_counter() - started) * 1_000


def _analyze(connection: Any, sql: Any, table_name: str) -> float:
    started = time.perf_counter()
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL("ANALYZE {}").format(sql.Identifier(table_name)))
    return (time.perf_counter() - started) * 1_000


def _measure_exact(
    connection: Any,
    sql: Any,
    numpy: Any,
    table_name: str,
    scalar_index_name: str,
    scale: int,
    config: ScaleBenchmarkConfig,
    *,
    data_load_ms: float,
    scalar_index_build_ms: float,
    analyze_ms: float,
) -> tuple[ScaleMeasurement, dict[int, tuple[str, ...]]]:
    _set_exact_plan(connection)
    latencies, recall, oracle = _measure_queries(
        connection,
        sql,
        numpy,
        table_name,
        config,
        expected=None,
    )
    for query_index, actual in oracle.items():
        expected = gold_chunk_ids(query_index, config)
        if actual != expected:
            raise RuntimeError(
                f"exact oracle diverged from synthetic gold for query {query_index}: {actual}"
            )
    explain = _explain_query(
        connection,
        sql,
        numpy,
        table_name,
        query_vector(0, config),
        config,
    )
    table_bytes, index_bytes, total_bytes = _storage_sizes(
        connection,
        table_name,
        scalar_index_name,
    )
    return (
        ScaleMeasurement(
            scale=scale,
            recall_at_10=recall,
            p50_ms=percentile(tuple(latencies), 0.50),
            p95_ms=percentile(tuple(latencies), 0.95),
            measured_queries=len(latencies),
            data_load_ms=data_load_ms,
            scalar_index_build_ms=scalar_index_build_ms,
            analyze_ms=analyze_ms,
            table_bytes=table_bytes,
            total_index_bytes=index_bytes,
            total_relation_bytes=total_bytes,
            explain=explain,
            backend_memory=_backend_memory(connection),
        ),
        oracle,
    )


def _measure_hnsw(
    connection: Any,
    sql: Any,
    numpy: Any,
    table_name: str,
    ef_search: int,
    oracle: dict[int, tuple[str, ...]],
    config: ScaleBenchmarkConfig,
) -> HnswMeasurement:
    with connection.cursor() as cursor:
        cursor.execute("SET enable_indexscan = on")
        cursor.execute("SET enable_bitmapscan = off")
        cursor.execute("SET enable_seqscan = off")
        cursor.execute("SELECT set_config('hnsw.ef_search', %s, false)", (str(ef_search),))
    latencies, recall, _actual = _measure_queries(
        connection,
        sql,
        numpy,
        table_name,
        config,
        expected=oracle,
    )
    explain = _explain_query(
        connection,
        sql,
        numpy,
        table_name,
        query_vector(0, config),
        config,
    )
    return HnswMeasurement(
        ef_search=ef_search,
        recall_at_10=recall,
        p50_ms=percentile(tuple(latencies), 0.50),
        p95_ms=percentile(tuple(latencies), 0.95),
        measured_queries=len(latencies),
        explain=explain,
        backend_memory=_backend_memory(connection),
    )


def _measure_queries(
    connection: Any,
    sql: Any,
    numpy: Any,
    table_name: str,
    config: ScaleBenchmarkConfig,
    *,
    expected: dict[int, tuple[str, ...]] | None,
) -> tuple[list[float], float, dict[int, tuple[str, ...]]]:
    statement = sql.SQL(
        "SELECT chunk_id FROM {} "
        "WHERE workspace_id=%s AND visibility=%s AND active "
        "AND embedding_dimensions=%s "
        "ORDER BY embedding <=> %s LIMIT %s"
    ).format(sql.Identifier(table_name))
    vectors = [
        numpy.asarray(query_vector(index, config), dtype=numpy.float32)
        for index in range(config.query_count)
    ]
    for _pass in range(config.warmup_passes):
        with connection.cursor() as cursor:
            for vector in vectors:
                cursor.execute(
                    statement,
                    (
                        "scale-benchmark",
                        "private",
                        config.dimensions,
                        vector,
                        config.top_k,
                    ),
                )
                cursor.fetchall()
    latencies: list[float] = []
    recall_values: list[float] = []
    latest: dict[int, tuple[str, ...]] = {}
    for _pass in range(config.measured_passes):
        with connection.cursor() as cursor:
            for query_index, vector in enumerate(vectors):
                started = time.perf_counter()
                cursor.execute(
                    statement,
                    (
                        "scale-benchmark",
                        "private",
                        config.dimensions,
                        vector,
                        config.top_k,
                    ),
                )
                actual = tuple(str(row[0]) for row in cursor.fetchall())
                latencies.append((time.perf_counter() - started) * 1_000)
                latest[query_index] = actual
                gold = (
                    gold_chunk_ids(query_index, config)
                    if expected is None
                    else expected[query_index]
                )
                recall_values.append(len(set(actual) & set(gold)) / config.top_k)
    return latencies, sum(recall_values) / len(recall_values), latest


def _set_exact_plan(connection: Any) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SET max_parallel_workers_per_gather = 0")
        cursor.execute("SET enable_indexscan = off")
        cursor.execute("SET enable_bitmapscan = off")
        cursor.execute("SET enable_seqscan = on")


def _explain_query(
    connection: Any,
    sql: Any,
    numpy: Any,
    table_name: str,
    vector: tuple[float, ...],
    config: ScaleBenchmarkConfig,
) -> dict[str, Any]:
    statement = sql.SQL(
        "EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON) "
        "SELECT chunk_id FROM {} "
        "WHERE workspace_id=%s AND visibility=%s AND active "
        "AND embedding_dimensions=%s "
        "ORDER BY embedding <=> %s LIMIT %s"
    ).format(sql.Identifier(table_name))
    with connection.cursor() as cursor:
        cursor.execute(
            statement,
            (
                "scale-benchmark",
                "private",
                config.dimensions,
                numpy.asarray(vector, dtype=numpy.float32),
                config.top_k,
            ),
        )
        raw = cursor.fetchone()[0]
    payload = raw[0] if isinstance(raw, list) else json.loads(raw)[0]
    plan = payload["Plan"]
    return {
        "planning_time_ms": float(payload.get("Planning Time", 0.0)),
        "execution_time_ms": float(payload.get("Execution Time", 0.0)),
        "plan_node_types": _plan_node_types(plan),
        "shared_hit_blocks": int(plan.get("Shared Hit Blocks", 0)),
        "shared_read_blocks": int(plan.get("Shared Read Blocks", 0)),
        "temp_read_blocks": int(plan.get("Temp Read Blocks", 0)),
        "temp_written_blocks": int(plan.get("Temp Written Blocks", 0)),
        "settings": payload.get("Settings", {}),
    }


def _plan_node_types(plan: dict[str, Any]) -> list[str]:
    result = [str(plan.get("Node Type", "unknown"))]
    for child in plan.get("Plans", []):
        result.extend(_plan_node_types(child))
    return result


def _backend_memory(connection: Any) -> dict[str, Any]:
    with connection.cursor() as cursor:
        try:
            cursor.execute(
                """
                WITH memory AS MATERIALIZED (
                    SELECT total_bytes, used_bytes FROM pg_backend_memory_contexts
                )
                SELECT COALESCE(SUM(total_bytes), 0), COALESCE(SUM(used_bytes), 0)
                FROM memory
                """
            )
            total_bytes, used_bytes = cursor.fetchone()
            return {
                "available": True,
                "retained_bytes": int(total_bytes),
                "used_bytes": int(used_bytes),
                "scope": "current PostgreSQL backend session after query",
            }
        except Exception as exc:
            return {
                "available": False,
                "reason": type(exc).__name__,
            }


def _storage_sizes(
    connection: Any,
    table_name: str,
    scalar_index_name: str,
) -> tuple[int, int, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_relation_size(%s::regclass), pg_indexes_size(%s::regclass), "
            "pg_total_relation_size(%s::regclass)",
            (table_name, table_name, table_name),
        )
        table_bytes, all_index_bytes, total_bytes = cursor.fetchone()
    scalar_bytes = _relation_size(connection, scalar_index_name)
    if int(all_index_bytes) < scalar_bytes:
        raise RuntimeError("PostgreSQL index size accounting is inconsistent")
    return int(table_bytes), int(all_index_bytes), int(total_bytes)


def _relation_size(connection: Any, relation_name: str) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_relation_size(%s::regclass)", (relation_name,))
        return int(cursor.fetchone()[0])


def _postgres_environment(connection: Any) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute("SHOW server_version")
        server_version = str(cursor.fetchone()[0])
        cursor.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
        pgvector_version = str(cursor.fetchone()[0])
        settings: dict[str, str] = {}
        for name in (
            "shared_buffers",
            "effective_cache_size",
            "maintenance_work_mem",
            "work_mem",
            "max_parallel_workers_per_gather",
        ):
            cursor.execute(f"SHOW {name}")
            settings[name] = str(cursor.fetchone()[0])
    return {
        "postgres_version": server_version,
        "pgvector_version": pgvector_version,
        "settings": settings,
    }


__all__ = [
    "DEFAULT_SCALE_BENCHMARK_CONFIG",
    "HnswGateDecision",
    "HnswMeasurement",
    "ScaleBenchmarkConfig",
    "ScaleMeasurement",
    "decide_hnsw_gate",
    "distractor_vector",
    "gold_chunk_ids",
    "percentile",
    "positive_vector",
    "query_vector",
    "run_scale_benchmark",
]
