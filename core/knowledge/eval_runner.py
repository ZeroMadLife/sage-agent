"""Layered, reproducible evaluation for the versioned Knowledge dataset."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
import time
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

from core.knowledge.datasets import (
    CorpusManifestEntry,
    EvalCase,
    VersionedKnowledgeDataset,
    load_versioned_dataset,
)
from core.knowledge.index import LocalKnowledgeIndex
from core.knowledge.postgres_index import (
    POSTGRES_DESCRIBED_PARENT_CHILD_SCHEMA_REVISION,
    PostgresKnowledgeIndex,
    PostgresKnowledgeIndexConfig,
)
from core.knowledge.recovery import KnowledgeRecoveryOutcome, KnowledgeRecoveryPolicy
from core.knowledge.retrieval import (
    DenseEmbeddingProvider,
    ExtractiveParentDescriptionProvider,
    HashingEmbeddingProvider,
    KnowledgeAblationPolicy,
    KnowledgeReranker,
    KnowledgeRetrievalMode,
    KnowledgeSearchHit,
    assemble_retrieval_bundle,
    chunk_document,
    embedding_text,
    lexical_terms,
    prepare_document_embeddings,
    prepare_query_embeddings,
)
from core.knowledge.store import KnowledgeSourceRoot, KnowledgeStore, PreparedKnowledgeSource

_FAILURE_LAYERS = (
    "none",
    "ingestion",
    "retrieval",
    "ranking",
    "context",
    "false_rejection",
    "false_acceptance",
    "grounding",
    "citation",
    "system",
)
EvalSplit = Literal["dev", "calibration", "test"]


@dataclass(frozen=True, slots=True)
class GateObservation:
    case_id: str
    answerable: bool
    score: float | None


@dataclass(frozen=True, slots=True)
class GateCalibration:
    threshold: float
    minimum_answerable_recall: float
    target_met: bool
    case_count: int
    answerable_count: int
    unanswerable_count: int
    metrics: dict[str, float | int]


@dataclass(frozen=True, slots=True)
class _RawCase:
    case: EvalCase
    hits: tuple[KnowledgeSearchHit, ...]
    latency_ms: float
    error_type: str | None = None
    recovery: KnowledgeRecoveryOutcome | None = None


def calibrate_gate(
    observations: Sequence[GateObservation],
    *,
    minimum_answerable_recall: float = 0.90,
) -> GateCalibration:
    """Choose one route threshold without looking outside the supplied calibration cases."""

    if not observations:
        raise ValueError("gate calibration requires observations")
    if not 0.0 <= minimum_answerable_recall <= 1.0:
        raise ValueError("minimum_answerable_recall must be between zero and one")
    ids = [item.case_id for item in observations]
    if len(ids) != len(set(ids)):
        raise ValueError("gate calibration case ids must be unique")
    answerable_count = sum(item.answerable for item in observations)
    unanswerable_count = len(observations) - answerable_count
    if answerable_count == 0 or unanswerable_count == 0:
        raise ValueError("gate calibration requires both answerable and unanswerable cases")
    scores = sorted({item.score for item in observations if item.score is not None})
    if not scores:
        raise ValueError("gate calibration requires at least one retrieval score")
    reject_all = scores[-1] + max(abs(scores[-1]) * 1e-9, 1e-12)
    candidates = (*scores, reject_all)
    evaluated = [(threshold, _gate_metrics(observations, threshold)) for threshold in candidates]
    eligible = [
        item
        for item in evaluated
        if float(item[1]["answerable_recall"]) >= minimum_answerable_recall
    ]
    target_met = bool(eligible)
    pool = eligible or [
        item
        for item in evaluated
        if item[1]["answerable_recall"]
        == max(candidate[1]["answerable_recall"] for candidate in evaluated)
    ]
    threshold, metrics = max(
        pool,
        key=lambda item: (
            float(item[1]["abstain_f1"]),
            float(item[1]["answerable_f1"]),
            float(item[1]["accuracy"]),
            item[0],
        ),
    )
    return GateCalibration(
        threshold=threshold,
        minimum_answerable_recall=minimum_answerable_recall,
        target_met=target_met,
        case_count=len(observations),
        answerable_count=answerable_count,
        unanswerable_count=unanswerable_count,
        metrics=metrics,
    )


def run_sqlite_layered_eval(
    repo_root: Path,
    dataset_path: Path,
    *,
    retrieval_modes: tuple[KnowledgeRetrievalMode, ...] = ("sparse", "dense", "hybrid"),
    top_k: int = 10,
    candidate_k: int = 50,
    token_budget: int = 3_000,
    provider: DenseEmbeddingProvider | None = None,
    minimum_answerable_recall: float = 0.90,
    evaluation_splits: tuple[EvalSplit, ...] = ("dev", "calibration", "test"),
    precache_queries: bool = True,
    gate_thresholds: Mapping[KnowledgeRetrievalMode, float] | None = None,
    recovery_policy: KnowledgeRecoveryPolicy | None = None,
    ablation_policy: KnowledgeAblationPolicy | None = None,
    reranker: KnowledgeReranker | None = None,
) -> dict[str, Any]:
    """Run the same frozen corpus through isolated SQLite retrieval routes."""

    root = repo_root.resolve()
    if not retrieval_modes or len(retrieval_modes) != len(set(retrieval_modes)):
        raise ValueError("retrieval modes must be non-empty and unique")
    if any(mode not in {"sparse", "dense", "hybrid"} for mode in retrieval_modes):
        raise ValueError("unsupported retrieval mode")
    _validate_gate_thresholds(retrieval_modes, gate_thresholds)
    if top_k < 1 or top_k > 50 or candidate_k < top_k or candidate_k > 50:
        raise ValueError("eval requires 1 <= top_k <= candidate_k <= 50")
    if token_budget < 256 or token_budget > 20_000:
        raise ValueError("eval token budget must be between 256 and 20000")

    dataset_file = dataset_path.resolve() if dataset_path.is_absolute() else root / dataset_path
    full_dataset = load_versioned_dataset(root, dataset_file)
    dataset = _select_evaluation_splits(full_dataset, evaluation_splits)
    embedding_provider = provider or HashingEmbeddingProvider()
    experiment = ablation_policy or KnowledgeAblationPolicy()
    _validate_ablation(experiment, reranker)
    source_commit = _git_value(root, "rev-parse", "HEAD")
    source_dirty = bool(_git_value(root, "status", "--porcelain"))
    snapshot_root = root / "knowledge" / "corpus" / "snapshots"

    with tempfile.TemporaryDirectory(prefix="sage-rag-layered-eval-") as temp:
        temporary = Path(temp)
        store = KnowledgeStore(
            temporary / "workspace",
            temporary / "knowledge.sqlite3",
            {
                "versioned-corpus": KnowledgeSourceRoot(
                    root_id="versioned-corpus",
                    kind="markdown",
                    label="Sage Versioned Corpus",
                    path=snapshot_root,
                )
            },
            knowledge_index=LocalKnowledgeIndex(
                workspace_id="sage-official-agent-fullstack-v1",
                embedding_provider=embedding_provider,
                ablation_policy=experiment,
            ),
            recovery_policy=recovery_policy,
            ablation_policy=experiment,
            reranker=reranker,
        )
        prepared = _prepare_corpus(store, root, snapshot_root, dataset)
        embedding_started = time.perf_counter()
        _prepare_provider(
            embedding_provider,
            prepared,
            dataset.cases if precache_queries else (),
            ablation_policy=experiment,
        )
        embedding_preparation_latency_ms = (time.perf_counter() - embedding_started) * 1_000
        reranker_preparation_latency_ms = _prepare_reranker(reranker)
        ingestion_started = time.perf_counter()
        for entry, source in prepared:
            try:
                proposal = store.ingest_prepared(source)
                store.approve(proposal.proposal_id, proposal.revision)
            except Exception as exc:
                raise RuntimeError(
                    f"failed to ingest approved corpus entry: {entry.corpus_id}"
                ) from exc
        ingestion_latency_ms = (time.perf_counter() - ingestion_started) * 1_000

        relative_to_entry = {
            Path(entry.snapshot_path).relative_to("knowledge/corpus/snapshots").as_posix(): entry
            for entry in dataset.corpus
        }
        routes = {
            mode: _run_route(
                store,
                dataset,
                relative_to_entry,
                retrieval_mode=mode,
                top_k=top_k,
                candidate_k=candidate_k,
                token_budget=token_budget,
                minimum_answerable_recall=minimum_answerable_recall,
                estimated_cost_usd=_estimated_cost(embedding_provider, reranker),
                gate_threshold=None if gate_thresholds is None else gate_thresholds[mode],
                semantic_provider=embedding_provider.supports_semantic_recall,
            )
            for mode in retrieval_modes
        }
        index = asdict(store.index_summary())
        ablation = _ablation_metadata(experiment, prepared, reranker)

    result: dict[str, Any] = {
        "schema_version": 1,
        "evaluation_id": (
            "sage-sqlite-layered-semantic-v1"
            if embedding_provider.supports_semantic_recall
            else "sage-sqlite-layered-baseline-v1"
        ),
        "dataset": {
            "dataset_id": dataset.manifest.dataset_id,
            "dataset_revision": dataset.manifest.dataset_revision,
            "case_count": len(dataset.cases),
            "full_case_count": len(full_dataset.cases),
            "corpus_count": len(dataset.corpus),
            "split_counts": dataset.split_counts,
            "manifest_split_counts": full_dataset.split_counts,
            "frozen_test": dataset.manifest.frozen_test,
            "frozen_test_evaluated": "test" in evaluation_splits,
        },
        "inputs": {
            "dataset_manifest_sha256": _sha256_file(dataset_file),
            "corpus_manifest_sha256": dataset.manifest.corpus_manifest_sha256,
            "cases_sha256": dataset.manifest.cases_sha256,
        },
        "source": {"commit": source_commit, "dirty": source_dirty},
        "backend": "sqlite-fts5+python-cosine",
        "provider": {
            "model_id": embedding_provider.model_id,
            "model_revision": embedding_provider.model_revision,
            "dimensions": embedding_provider.dimensions,
            "supports_semantic_recall": embedding_provider.supports_semantic_recall,
            "label": (
                "deterministic feature hashing; not semantic retrieval"
                if not embedding_provider.supports_semantic_recall
                else "semantic embedding provider"
            ),
        },
        "ablation": ablation,
        "parameters": {
            "top_k": top_k,
            "candidate_k": candidate_k,
            "token_budget": token_budget,
            "minimum_answerable_recall": minimum_answerable_recall,
            "evaluation_splits": list(evaluation_splits),
            "queries_precached": precache_queries,
            "gate_thresholds": None if gate_thresholds is None else dict(gate_thresholds),
            "test_split_used_for_calibration": False,
            "recovery_policy": asdict(recovery_policy or KnowledgeRecoveryPolicy()),
        },
        "embedding_preparation": {
            "latency_ms": round(embedding_preparation_latency_ms, 3),
            "queries_included": precache_queries,
        },
        "reranker_preparation": {
            "latency_ms": round(reranker_preparation_latency_ms, 3),
            "included_in_query_latency": False,
        },
        "ingestion": {
            "approved_source_count": len(dataset.corpus),
            "failure_count": 0,
            "latency_ms": round(ingestion_latency_ms, 3),
        },
        "index": index,
        "routes": routes,
    }
    result["deterministic_digest"] = _deterministic_digest(result)
    return result


def run_postgres_layered_eval(
    repo_root: Path,
    dataset_path: Path,
    *,
    postgres_dsn: str,
    retrieval_modes: tuple[KnowledgeRetrievalMode, ...] = ("sparse", "dense", "hybrid"),
    top_k: int = 10,
    candidate_k: int = 50,
    token_budget: int = 3_000,
    provider: DenseEmbeddingProvider | None = None,
    minimum_answerable_recall: float = 0.90,
    evaluation_splits: tuple[EvalSplit, ...] = ("dev", "calibration", "test"),
    precache_queries: bool = True,
    gate_thresholds: Mapping[KnowledgeRetrievalMode, float] | None = None,
    recovery_policy: KnowledgeRecoveryPolicy | None = None,
    ablation_policy: KnowledgeAblationPolicy | None = None,
    reranker: KnowledgeReranker | None = None,
) -> dict[str, Any]:
    """Run the frozen corpus through GIN and pgvector exact retrieval routes."""

    root = repo_root.resolve()
    if not retrieval_modes or len(retrieval_modes) != len(set(retrieval_modes)):
        raise ValueError("retrieval modes must be non-empty and unique")
    if any(mode not in {"sparse", "dense", "hybrid"} for mode in retrieval_modes):
        raise ValueError("unsupported retrieval mode")
    _validate_gate_thresholds(retrieval_modes, gate_thresholds)
    if top_k < 1 or top_k > 50 or candidate_k < top_k or candidate_k > 50:
        raise ValueError("eval requires 1 <= top_k <= candidate_k <= 50")
    if token_budget < 256 or token_budget > 20_000:
        raise ValueError("eval token budget must be between 256 and 20000")

    dataset_file = dataset_path.resolve() if dataset_path.is_absolute() else root / dataset_path
    full_dataset = load_versioned_dataset(root, dataset_file)
    dataset = _select_evaluation_splits(full_dataset, evaluation_splits)
    embedding_provider = provider or HashingEmbeddingProvider()
    experiment = ablation_policy or KnowledgeAblationPolicy()
    _validate_ablation(experiment, reranker)
    source_commit = _git_value(root, "rev-parse", "HEAD")
    source_dirty = bool(_git_value(root, "status", "--porcelain"))
    snapshot_root = root / "knowledge" / "corpus" / "snapshots"
    workspace_id = f"sage-eval-{uuid.uuid4().hex}"
    postgres_index = PostgresKnowledgeIndex(
        PostgresKnowledgeIndexConfig(dsn=postgres_dsn),
        workspace_id=workspace_id,
        embedding_provider=embedding_provider,
        ablation_policy=experiment,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="sage-rag-postgres-eval-") as temp:
            temporary = Path(temp)
            store = KnowledgeStore(
                temporary / "workspace",
                temporary / "canonical.sqlite3",
                {
                    "versioned-corpus": KnowledgeSourceRoot(
                        root_id="versioned-corpus",
                        kind="markdown",
                        label="Sage Versioned Corpus",
                        path=snapshot_root,
                    )
                },
                knowledge_index=postgres_index,
                recovery_policy=recovery_policy,
                ablation_policy=experiment,
                reranker=reranker,
            )
            prepared = _prepare_corpus(store, root, snapshot_root, dataset)
            embedding_started = time.perf_counter()
            _prepare_provider(
                embedding_provider,
                prepared,
                dataset.cases if precache_queries else (),
                ablation_policy=experiment,
            )
            embedding_preparation_latency_ms = (time.perf_counter() - embedding_started) * 1_000
            reranker_preparation_latency_ms = _prepare_reranker(reranker)
            ingestion_started = time.perf_counter()
            for entry, source in prepared:
                try:
                    proposal = store.ingest_prepared(source)
                    store.approve(proposal.proposal_id, proposal.revision)
                except Exception as exc:
                    raise RuntimeError(
                        f"failed to ingest approved corpus entry: {entry.corpus_id}"
                    ) from exc
            ingestion_latency_ms = (time.perf_counter() - ingestion_started) * 1_000
            relative_to_entry = {
                Path(entry.snapshot_path)
                .relative_to("knowledge/corpus/snapshots")
                .as_posix(): entry
                for entry in dataset.corpus
            }
            routes = {
                mode: _run_route(
                    store,
                    dataset,
                    relative_to_entry,
                    retrieval_mode=mode,
                    top_k=top_k,
                    candidate_k=candidate_k,
                    token_budget=token_budget,
                    minimum_answerable_recall=minimum_answerable_recall,
                    estimated_cost_usd=_estimated_cost(embedding_provider, reranker),
                    gate_threshold=None if gate_thresholds is None else gate_thresholds[mode],
                    semantic_provider=embedding_provider.supports_semantic_recall,
                )
                for mode in retrieval_modes
            }
            route_labels = {
                "sparse": "PostgreSQL GIN + ts_rank_cd sparse-only; not BM25",
                "dense": (
                    "pgvector exact semantic dense-only"
                    if embedding_provider.supports_semantic_recall
                    else "pgvector exact Hashing dense-only; deterministic and non-semantic"
                ),
                "hybrid": (
                    "PostgreSQL GIN + pgvector exact semantic dense + RRF"
                    if embedding_provider.supports_semantic_recall
                    else "PostgreSQL GIN + constrained pgvector exact Hashing + RRF"
                ),
            }
            for mode, route in routes.items():
                route["label"] = route_labels[mode]
            index = asdict(store.index_summary())
            storage = postgres_index.storage_summary()
            ablation = _ablation_metadata(experiment, prepared, reranker)

        result: dict[str, Any] = {
            "schema_version": 1,
            "evaluation_id": (
                "sage-postgres-exact-layered-semantic-v1"
                if embedding_provider.supports_semantic_recall
                else "sage-postgres-exact-layered-baseline-v1"
            ),
            "dataset": {
                "dataset_id": dataset.manifest.dataset_id,
                "dataset_revision": dataset.manifest.dataset_revision,
                "case_count": len(dataset.cases),
                "full_case_count": len(full_dataset.cases),
                "corpus_count": len(dataset.corpus),
                "split_counts": dataset.split_counts,
                "manifest_split_counts": full_dataset.split_counts,
                "frozen_test": dataset.manifest.frozen_test,
                "frozen_test_evaluated": "test" in evaluation_splits,
            },
            "inputs": {
                "dataset_manifest_sha256": _sha256_file(dataset_file),
                "corpus_manifest_sha256": dataset.manifest.corpus_manifest_sha256,
                "cases_sha256": dataset.manifest.cases_sha256,
            },
            "source": {"commit": source_commit, "dirty": source_dirty},
            "backend": "postgres-tsvector+pgvector-exact",
            "backend_schema_revision": POSTGRES_DESCRIBED_PARENT_CHILD_SCHEMA_REVISION,
            "provider": {
                "model_id": embedding_provider.model_id,
                "model_revision": embedding_provider.model_revision,
                "dimensions": embedding_provider.dimensions,
                "supports_semantic_recall": embedding_provider.supports_semantic_recall,
                "label": (
                    "deterministic feature hashing; not semantic retrieval"
                    if not embedding_provider.supports_semantic_recall
                    else "semantic embedding provider"
                ),
            },
            "ablation": ablation,
            "parameters": {
                "top_k": top_k,
                "candidate_k": candidate_k,
                "token_budget": token_budget,
                "minimum_answerable_recall": minimum_answerable_recall,
                "evaluation_splits": list(evaluation_splits),
                "queries_precached": precache_queries,
                "gate_thresholds": None if gate_thresholds is None else dict(gate_thresholds),
                "test_split_used_for_calibration": False,
                "recovery_policy": asdict(recovery_policy or KnowledgeRecoveryPolicy()),
                "ann_index_used": False,
            },
            "embedding_preparation": {
                "latency_ms": round(embedding_preparation_latency_ms, 3),
                "queries_included": precache_queries,
            },
            "reranker_preparation": {
                "latency_ms": round(reranker_preparation_latency_ms, 3),
                "included_in_query_latency": False,
            },
            "ingestion": {
                "approved_source_count": len(dataset.corpus),
                "failure_count": 0,
                "latency_ms": round(ingestion_latency_ms, 3),
            },
            "index": index,
            "storage": storage,
            "routes": routes,
        }
        result["deterministic_digest"] = _deterministic_digest(result)
        return result
    finally:
        with suppress(Exception):
            postgres_index.delete_workspace()
        postgres_index.close()


def compare_bounded_recovery_reports(
    baseline_report: dict[str, Any],
    candidate_report: dict[str, Any],
    *,
    route: KnowledgeRetrievalMode = "hybrid",
    maximum_p95_latency_ms: float = 100.0,
) -> dict[str, Any]:
    """Apply frozen PR-5B gates without changing the baseline relevance threshold."""

    compatibility_fields = (
        (
            "dataset_id",
            baseline_report["dataset"]["dataset_id"],
            candidate_report["dataset"]["dataset_id"],
        ),
        (
            "dataset_revision",
            baseline_report["dataset"]["dataset_revision"],
            candidate_report["dataset"]["dataset_revision"],
        ),
        (
            "cases_sha256",
            baseline_report["inputs"]["cases_sha256"],
            candidate_report["inputs"]["cases_sha256"],
        ),
        ("top_k", baseline_report["parameters"]["top_k"], candidate_report["parameters"]["top_k"]),
        (
            "candidate_k",
            baseline_report["parameters"]["candidate_k"],
            candidate_report["parameters"]["candidate_k"],
        ),
        (
            "evaluation_splits",
            baseline_report["parameters"]["evaluation_splits"],
            candidate_report["parameters"]["evaluation_splits"],
        ),
    )
    mismatches = [
        name for name, baseline, candidate in compatibility_fields if baseline != candidate
    ]
    if mismatches:
        raise ValueError("incompatible recovery eval inputs: " + ", ".join(mismatches))
    baseline_route = baseline_report["routes"][route]
    candidate_route = candidate_report["routes"][route]
    baseline_threshold = float(baseline_route["gate"]["threshold"])
    candidate_threshold = float(candidate_route["gate"]["threshold"])
    baseline_cases = {str(item["case_id"]): item for item in baseline_route["cases"]}
    candidate_cases = {str(item["case_id"]): item for item in candidate_route["cases"]}
    if baseline_cases.keys() != candidate_cases.keys():
        raise ValueError("recovery eval reports cover different cases")
    failure_ids = tuple(
        case_id
        for case_id, item in baseline_cases.items()
        if item["primary_failure"] == "retrieval" and item["answerable"]
    )

    def subset_recall(cases: dict[str, dict[str, Any]]) -> float | None:
        if not failure_ids:
            return None
        return sum(
            float(cases[case_id]["retrieval"]["recall_at_k"]) for case_id in failure_ids
        ) / len(failure_ids)

    baseline_subset = subset_recall(baseline_cases)
    candidate_subset = subset_recall(candidate_cases)
    subset_passed = baseline_subset is None or (
        candidate_subset is not None and candidate_subset > baseline_subset
    )
    baseline_false_acceptance = sum(
        item["primary_failure"] == "false_acceptance" for item in baseline_cases.values()
    )
    candidate_false_acceptance = sum(
        item["primary_failure"] == "false_acceptance" for item in candidate_cases.values()
    )
    regressions = [
        case_id
        for case_id in baseline_cases
        if float(candidate_cases[case_id]["retrieval"]["recall_at_k"])
        < float(baseline_cases[case_id]["retrieval"]["recall_at_k"])
    ]
    max_rounds = max(
        (int(item["trace"]["recovery"]["round_count"]) for item in candidate_cases.values()),
        default=0,
    )
    candidate_p95 = float(candidate_route["system"]["latency_ms"]["p95"])
    raw_cost = candidate_route["system"]["estimated_cost_usd"]
    estimated_cost = None if raw_cost is None else float(raw_cost)
    gates: dict[str, dict[str, Any]] = {
        "retrieval_failure_subset_recall": {
            "case_ids": list(failure_ids),
            "baseline": baseline_subset,
            "candidate": candidate_subset,
            "passed": subset_passed,
        },
        "false_acceptance": {
            "baseline": baseline_false_acceptance,
            "candidate": candidate_false_acceptance,
            "delta": candidate_false_acceptance - baseline_false_acceptance,
            "passed": candidate_false_acceptance <= baseline_false_acceptance,
        },
        "gate_threshold_unchanged": {
            "baseline": baseline_threshold,
            "candidate": candidate_threshold,
            "passed": candidate_threshold == baseline_threshold,
        },
        "retrieval_regressions": {
            "case_ids": regressions,
            "passed": not regressions,
        },
        "maximum_rounds": {"maximum": 2, "actual": max_rounds, "passed": max_rounds <= 2},
        "p95_latency_ms": {
            "maximum": maximum_p95_latency_ms,
            "actual": candidate_p95,
            "passed": candidate_p95 <= maximum_p95_latency_ms,
        },
        "estimated_cost_usd": {
            "maximum": 0.0,
            "actual": estimated_cost,
            "passed": estimated_cost == 0.0,
        },
    }
    return {
        "compatible_inputs": True,
        "route": route,
        "overall_passed": all(item["passed"] for item in gates.values()),
        "gates": gates,
    }


def compare_retrieval_ablation_reports(
    baseline_report: dict[str, Any],
    candidate_report: dict[str, Any],
    *,
    route: KnowledgeRetrievalMode = "hybrid",
    minimum_target_delta: float = 0.01,
    minimum_recall_delta: float = -0.001,
    minimum_citation_support_delta: float = -0.001,
    maximum_p95_latency_ms: float = 250.0,
    maximum_estimated_cost_usd: float = 0.01,
    maximum_chunk_multiplier: float = 4.0,
    maximum_storage_multiplier: float = 4.0,
) -> dict[str, Any]:
    """Gate one PR-6 strategy without allowing combined-candidate attribution."""

    compatibility_fields = (
        (
            "dataset_id",
            baseline_report["dataset"]["dataset_id"],
            candidate_report["dataset"]["dataset_id"],
        ),
        (
            "dataset_revision",
            baseline_report["dataset"]["dataset_revision"],
            candidate_report["dataset"]["dataset_revision"],
        ),
        (
            "cases_sha256",
            baseline_report["inputs"]["cases_sha256"],
            candidate_report["inputs"]["cases_sha256"],
        ),
        ("backend", baseline_report["backend"], candidate_report["backend"]),
        (
            "backend_schema_revision",
            baseline_report.get("backend_schema_revision"),
            candidate_report.get("backend_schema_revision"),
        ),
        (
            "provider_model",
            baseline_report["provider"]["model_id"],
            candidate_report["provider"]["model_id"],
        ),
        (
            "provider_revision",
            baseline_report["provider"]["model_revision"],
            candidate_report["provider"]["model_revision"],
        ),
        (
            "top_k",
            baseline_report["parameters"]["top_k"],
            candidate_report["parameters"]["top_k"],
        ),
        (
            "candidate_k",
            baseline_report["parameters"]["candidate_k"],
            candidate_report["parameters"]["candidate_k"],
        ),
        (
            "token_budget",
            baseline_report["parameters"]["token_budget"],
            candidate_report["parameters"]["token_budget"],
        ),
        (
            "evaluation_splits",
            baseline_report["parameters"]["evaluation_splits"],
            candidate_report["parameters"]["evaluation_splits"],
        ),
    )
    mismatches = [
        name for name, baseline, candidate in compatibility_fields if baseline != candidate
    ]
    if mismatches:
        raise ValueError("incompatible ablation eval inputs: " + ", ".join(mismatches))
    if baseline_report["ablation"]["strategy"] != "baseline":
        raise ValueError("ablation comparison baseline must use the baseline strategy")
    strategy = str(candidate_report["ablation"]["strategy"])
    if strategy not in {
        "contextual_chunk",
        "parent_child",
        "described_parent_child",
        "semantic_boundary",
        "cross_encoder",
    }:
        raise ValueError("ablation comparison requires one named candidate strategy")

    baseline_route = baseline_report["routes"][route]
    candidate_route = candidate_report["routes"][route]
    recall_delta = _rounded_delta(
        candidate_route["retrieval"]["recall_at_k"],
        baseline_route["retrieval"]["recall_at_k"],
    )
    ndcg_delta = _rounded_delta(
        candidate_route["ranking"]["ndcg_at_k"],
        baseline_route["ranking"]["ndcg_at_k"],
    )
    mrr_delta = _rounded_delta(candidate_route["ranking"]["mrr"], baseline_route["ranking"]["mrr"])
    claim_delta = _rounded_delta(
        candidate_route["generation"]["required_claim_token_recall"],
        baseline_route["generation"]["required_claim_token_recall"],
    )
    citation_delta = _rounded_delta(
        candidate_route["citation"]["support_rate"],
        baseline_route["citation"]["support_rate"],
    )
    if strategy == "cross_encoder":
        target_name = "ndcg_delta"
        target_delta = ndcg_delta
    elif strategy in {"parent_child", "described_parent_child"}:
        target_name = "max_recall_ndcg_or_claim_delta"
        target_delta = max(recall_delta, ndcg_delta, claim_delta)
    else:
        target_name = "max_recall_or_ndcg_delta"
        target_delta = max(recall_delta, ndcg_delta)
    target_metric: dict[str, str | float | int | bool | None] = {
        "name": target_name,
        "minimum_delta": minimum_target_delta,
        "delta": target_delta,
        "passed": target_delta >= minimum_target_delta,
    }

    false_acceptance_delta = int(candidate_route["failures"]["false_acceptance"]) - int(
        baseline_route["failures"]["false_acceptance"]
    )
    p95_latency_ms = float(candidate_route["system"]["latency_ms"]["p95"])
    raw_cost = candidate_route["system"]["estimated_cost_usd"]
    estimated_cost_usd = None if raw_cost is None else float(raw_cost)
    baseline_chunks = int(baseline_report["index"]["active_chunk_count"])
    candidate_chunks = int(candidate_report["index"]["active_chunk_count"])
    chunk_multiplier = candidate_chunks / baseline_chunks if baseline_chunks else math.inf
    baseline_storage = int(baseline_report["storage"]["workspace_row_bytes"])
    candidate_storage = int(candidate_report["storage"]["workspace_row_bytes"])
    storage_multiplier = candidate_storage / baseline_storage if baseline_storage else math.inf
    eligible_semantic_blocks = int(
        candidate_report["ablation"]["corpus_profile"]["semantic_boundary_eligible_block_count"]
    )
    strategy_exercised = (
        eligible_semantic_blocks
        if strategy in {"semantic_boundary", "described_parent_child"}
        else 1
    )
    gates: dict[str, dict[str, str | float | int | bool | None]] = {
        "target_gain": target_metric,
        "recall_no_regression": {
            "minimum_delta": minimum_recall_delta,
            "delta": recall_delta,
            "passed": recall_delta >= minimum_recall_delta,
        },
        "citation_support": {
            "minimum_delta": minimum_citation_support_delta,
            "delta": citation_delta,
            "passed": citation_delta >= minimum_citation_support_delta,
        },
        "false_acceptance": {
            "maximum_delta": 0,
            "delta": false_acceptance_delta,
            "passed": false_acceptance_delta <= 0,
        },
        "p95_latency_ms": {
            "maximum": maximum_p95_latency_ms,
            "actual": p95_latency_ms,
            "passed": p95_latency_ms <= maximum_p95_latency_ms,
        },
        "estimated_cost_usd": {
            "maximum": maximum_estimated_cost_usd,
            "actual": estimated_cost_usd,
            "passed": estimated_cost_usd is not None
            and estimated_cost_usd <= maximum_estimated_cost_usd,
        },
        "chunk_multiplier": {
            "maximum": maximum_chunk_multiplier,
            "actual": round(chunk_multiplier, 6),
            "passed": chunk_multiplier <= maximum_chunk_multiplier,
        },
        "storage_multiplier": {
            "maximum": maximum_storage_multiplier,
            "actual": round(storage_multiplier, 6),
            "passed": storage_multiplier <= maximum_storage_multiplier,
        },
        "strategy_exercised": {
            "minimum": 1,
            "actual": strategy_exercised,
            "passed": strategy_exercised >= 1,
        },
    }
    return {
        "compatible_inputs": True,
        "strategy": strategy,
        "route": route,
        "overall_passed": all(gate["passed"] is True for gate in gates.values()),
        "target_metric": target_metric,
        "deltas": {
            "recall_at_k": recall_delta,
            "mrr": mrr_delta,
            "ndcg_at_k": ndcg_delta,
            "required_claim_token_recall": claim_delta,
            "citation_support": citation_delta,
            "false_acceptance": false_acceptance_delta,
        },
        "gates": gates,
    }


def compare_layered_reports(
    sqlite_report: dict[str, Any],
    postgres_report: dict[str, Any],
    *,
    recall_tolerance: float = 0.02,
) -> dict[str, Any]:
    """Compare compatible routes and expose case-level retrieval regressions."""

    if recall_tolerance < 0.0 or recall_tolerance > 1.0:
        raise ValueError("recall tolerance must be between zero and one")
    compatibility_fields = {
        "dataset_id": (
            sqlite_report["dataset"]["dataset_id"],
            postgres_report["dataset"]["dataset_id"],
        ),
        "dataset_revision": (
            sqlite_report["dataset"]["dataset_revision"],
            postgres_report["dataset"]["dataset_revision"],
        ),
        "cases_sha256": (
            sqlite_report["inputs"]["cases_sha256"],
            postgres_report["inputs"]["cases_sha256"],
        ),
        "provider_model": (
            sqlite_report["provider"]["model_id"],
            postgres_report["provider"]["model_id"],
        ),
        "provider_revision": (
            sqlite_report["provider"]["model_revision"],
            postgres_report["provider"]["model_revision"],
        ),
        "top_k": (
            sqlite_report["parameters"]["top_k"],
            postgres_report["parameters"]["top_k"],
        ),
        "candidate_k": (
            sqlite_report["parameters"]["candidate_k"],
            postgres_report["parameters"]["candidate_k"],
        ),
    }
    mismatches = [
        field
        for field, (sqlite_value, postgres_value) in compatibility_fields.items()
        if sqlite_value != postgres_value
    ]
    if mismatches:
        raise ValueError("incompatible layered eval inputs: " + ", ".join(mismatches))

    common_modes = tuple(
        mode for mode in sqlite_report["routes"] if mode in postgres_report["routes"]
    )
    if not common_modes:
        raise ValueError("layered eval reports have no common retrieval routes")
    routes: dict[str, Any] = {}
    for mode in common_modes:
        sqlite_route = sqlite_report["routes"][mode]
        postgres_route = postgres_report["routes"][mode]
        sqlite_recall = float(sqlite_route["retrieval"]["recall_at_k"])
        postgres_recall = float(postgres_route["retrieval"]["recall_at_k"])
        sqlite_cases = {str(item["case_id"]): item for item in sqlite_route["cases"]}
        postgres_cases = {str(item["case_id"]): item for item in postgres_route["cases"]}
        regressions = []
        for case_id in sorted(sqlite_cases.keys() & postgres_cases.keys()):
            sqlite_case = sqlite_cases[case_id]
            postgres_case = postgres_cases[case_id]
            case_delta = float(postgres_case["retrieval"]["recall_at_k"]) - float(
                sqlite_case["retrieval"]["recall_at_k"]
            )
            if case_delta < 0.0:
                regressions.append(
                    {
                        "case_id": case_id,
                        "sqlite_failure": sqlite_case["primary_failure"],
                        "postgres_failure": postgres_case["primary_failure"],
                        "recall_at_k_delta": round(case_delta, 6),
                    }
                )
        recall_delta = postgres_recall - sqlite_recall
        routes[mode] = {
            "sqlite": {
                "recall_at_k": sqlite_recall,
                "mrr": float(sqlite_route["ranking"]["mrr"]),
                "ndcg_at_k": float(sqlite_route["ranking"]["ndcg_at_k"]),
                "p50_latency_ms": float(sqlite_route["system"]["latency_ms"]["p50"]),
                "p95_latency_ms": float(sqlite_route["system"]["latency_ms"]["p95"]),
            },
            "postgres": {
                "recall_at_k": postgres_recall,
                "mrr": float(postgres_route["ranking"]["mrr"]),
                "ndcg_at_k": float(postgres_route["ranking"]["ndcg_at_k"]),
                "p50_latency_ms": float(postgres_route["system"]["latency_ms"]["p50"]),
                "p95_latency_ms": float(postgres_route["system"]["latency_ms"]["p95"]),
            },
            "recall_at_k_delta": round(recall_delta, 6),
            "mrr_delta": round(
                float(postgres_route["ranking"]["mrr"]) - float(sqlite_route["ranking"]["mrr"]),
                6,
            ),
            "ndcg_at_k_delta": round(
                float(postgres_route["ranking"]["ndcg_at_k"])
                - float(sqlite_route["ranking"]["ndcg_at_k"]),
                6,
            ),
            "recall_gate_passed": recall_delta >= -recall_tolerance,
            "regressions": regressions,
        }
    return {
        "compatible_inputs": True,
        "recall_tolerance": recall_tolerance,
        "overall_passed": all(route["recall_gate_passed"] for route in routes.values()),
        "routes": routes,
    }


def compare_semantic_provider_reports(
    baseline_report: dict[str, Any],
    candidate_report: dict[str, Any],
    *,
    route: KnowledgeRetrievalMode = "hybrid",
    minimum_semantic_paraphrase_recall_delta: float = 0.05,
    minimum_overall_recall_delta: float = -0.01,
    minimum_abstain_f1_delta: float = -0.05,
    minimum_citation_support_delta: float = -0.001,
    maximum_p95_latency_ms: float = 100.0,
    maximum_estimated_cost_usd: float = 0.01,
    evaluation_split: EvalSplit | None = None,
) -> dict[str, Any]:
    """Apply the frozen PR-4 activation gates to one semantic candidate."""

    compatibility_fields = (
        (
            "dataset_id",
            baseline_report["dataset"]["dataset_id"],
            candidate_report["dataset"]["dataset_id"],
        ),
        (
            "dataset_revision",
            baseline_report["dataset"]["dataset_revision"],
            candidate_report["dataset"]["dataset_revision"],
        ),
        (
            "cases_sha256",
            baseline_report["inputs"]["cases_sha256"],
            candidate_report["inputs"]["cases_sha256"],
        ),
        ("top_k", baseline_report["parameters"]["top_k"], candidate_report["parameters"]["top_k"]),
        (
            "candidate_k",
            baseline_report["parameters"]["candidate_k"],
            candidate_report["parameters"]["candidate_k"],
        ),
        (
            "evaluation_splits",
            baseline_report["parameters"]["evaluation_splits"],
            candidate_report["parameters"]["evaluation_splits"],
        ),
    )
    mismatches = [
        name for name, baseline, candidate in compatibility_fields if baseline != candidate
    ]
    if mismatches:
        raise ValueError("incompatible semantic eval inputs: " + ", ".join(mismatches))
    if baseline_report["provider"]["supports_semantic_recall"]:
        raise ValueError("semantic comparison baseline must be non-semantic")
    if not candidate_report["provider"]["supports_semantic_recall"]:
        raise ValueError("semantic comparison candidate must support semantic recall")

    baseline_route = baseline_report["routes"][route]
    candidate_route = candidate_report["routes"][route]
    baseline_metrics = (
        baseline_route if evaluation_split is None else baseline_route["splits"][evaluation_split]
    )
    candidate_metrics = (
        candidate_route if evaluation_split is None else candidate_route["splits"][evaluation_split]
    )
    baseline_semantic_recall: float | None
    candidate_semantic_recall: float | None
    if evaluation_split is None:
        baseline_semantic_recall = float(
            baseline_route["categories"]["semantic_paraphrase"]["retrieval"]["recall_at_k"]
        )
        candidate_semantic_recall = float(
            candidate_route["categories"]["semantic_paraphrase"]["retrieval"]["recall_at_k"]
        )
        semantic_case_count = int(
            candidate_route["categories"]["semantic_paraphrase"].get("case_count", 0)
        )
    else:
        baseline_semantic_recall, baseline_count = _category_recall(
            baseline_route, evaluation_split, "semantic_paraphrase"
        )
        candidate_semantic_recall, semantic_case_count = _category_recall(
            candidate_route, evaluation_split, "semantic_paraphrase"
        )
        if baseline_count != semantic_case_count:
            raise ValueError("semantic eval reports have different category coverage")
    semantic_delta = (
        None
        if baseline_semantic_recall is None or candidate_semantic_recall is None
        else _rounded_delta(candidate_semantic_recall, baseline_semantic_recall)
    )
    overall_delta = _rounded_delta(
        candidate_metrics["retrieval"]["recall_at_k"],
        baseline_metrics["retrieval"]["recall_at_k"],
    )
    abstain_delta = _rounded_delta(
        (
            candidate_route["gate"]["evaluation"]["abstain_f1"]
            if evaluation_split is None
            else candidate_metrics["gate"]["abstain_f1"]
        ),
        (
            baseline_route["gate"]["evaluation"]["abstain_f1"]
            if evaluation_split is None
            else baseline_metrics["gate"]["abstain_f1"]
        ),
    )
    citation_delta = _rounded_delta(
        candidate_metrics["citation"]["support_rate"],
        baseline_metrics["citation"]["support_rate"],
    )
    p95_latency_ms = (
        float(candidate_route["system"]["latency_ms"]["p95"])
        if evaluation_split is None
        else _percentile(
            [
                float(case["system"]["latency_ms"])
                for case in candidate_route["cases"]
                if case["dataset_split"] == evaluation_split
            ],
            0.95,
        )
    )
    raw_cost = candidate_route["system"]["estimated_cost_usd"]
    estimated_cost_usd = None if raw_cost is None else float(raw_cost)
    semantic_gate: dict[str, float | int | bool | str | None] = {
        "minimum_delta": minimum_semantic_paraphrase_recall_delta,
        "delta": semantic_delta,
        "passed": semantic_delta is not None
        and semantic_delta >= minimum_semantic_paraphrase_recall_delta,
    }
    if evaluation_split is not None:
        semantic_gate.update(
            {
                "case_count": semantic_case_count,
                "baseline": baseline_semantic_recall,
                "candidate": candidate_semantic_recall,
            }
        )
        if semantic_case_count == 0:
            semantic_gate["reason"] = "evaluation split has no semantic_paraphrase cases"
    gates: dict[str, dict[str, float | int | bool | str | None]] = {
        "semantic_paraphrase_recall": semantic_gate,
        "overall_recall": {
            "minimum_delta": minimum_overall_recall_delta,
            "delta": overall_delta,
            "passed": overall_delta >= minimum_overall_recall_delta,
        },
        "abstain_f1": {
            "minimum_delta": minimum_abstain_f1_delta,
            "delta": abstain_delta,
            "passed": abstain_delta >= minimum_abstain_f1_delta,
        },
        "citation_support": {
            "minimum_delta": minimum_citation_support_delta,
            "delta": citation_delta,
            "passed": citation_delta >= minimum_citation_support_delta,
        },
        "p95_latency_ms": {
            "maximum": maximum_p95_latency_ms,
            "actual": p95_latency_ms,
            "passed": p95_latency_ms <= maximum_p95_latency_ms,
        },
        "estimated_cost_usd": {
            "maximum": maximum_estimated_cost_usd,
            "actual": estimated_cost_usd,
            "passed": estimated_cost_usd is not None
            and estimated_cost_usd <= maximum_estimated_cost_usd,
        },
    }
    return {
        "compatible_inputs": True,
        "route": route,
        "evaluation_split": evaluation_split,
        "overall_passed": all(gate.get("passed") is True for gate in gates.values()),
        "gates": gates,
    }


def _rounded_delta(candidate: str | int | float, baseline: str | int | float) -> float:
    return round(float(candidate) - float(baseline), 6)


def _category_recall(
    route: dict[str, Any], split: EvalSplit, category: str
) -> tuple[float | None, int]:
    cases = [
        case
        for case in route["cases"]
        if case["dataset_split"] == split
        and case["category"] == category
        and bool(case["answerable"])
    ]
    if not cases:
        return None, 0
    return (
        sum(float(case["retrieval"]["recall_at_k"]) for case in cases) / len(cases),
        len(cases),
    )


def _select_evaluation_splits(
    dataset: VersionedKnowledgeDataset,
    splits: tuple[EvalSplit, ...],
) -> VersionedKnowledgeDataset:
    allowed = {"dev", "calibration", "test"}
    if (
        not splits
        or len(splits) != len(set(splits))
        or any(split not in allowed for split in splits)
    ):
        raise ValueError("evaluation splits must be non-empty, unique, and supported")
    if "calibration" not in splits:
        raise ValueError("evaluation splits must include calibration")
    selected = tuple(case for case in dataset.cases if case.dataset_split in splits)
    return replace(dataset, cases=selected)


def _validate_gate_thresholds(
    modes: tuple[KnowledgeRetrievalMode, ...],
    thresholds: Mapping[KnowledgeRetrievalMode, float] | None,
) -> None:
    if thresholds is None:
        return
    if set(thresholds) != set(modes):
        raise ValueError("fixed gate thresholds must match the evaluated retrieval modes")
    if any(
        isinstance(value, bool) or not math.isfinite(value) or value < 0.0
        for value in thresholds.values()
    ):
        raise ValueError("fixed gate thresholds must be finite and non-negative")


def _estimated_cost(
    provider: DenseEmbeddingProvider,
    reranker: KnowledgeReranker | None = None,
) -> float | None:
    value = getattr(provider, "estimated_cost_usd", None)
    provider_cost = (
        float(value)
        if value is not None
        else 0.0
        if provider.model_id == HashingEmbeddingProvider.model_id
        else None
    )
    reranker_cost: float | None
    if reranker is None:
        reranker_cost = 0.0
    else:
        raw_reranker_cost = reranker.estimated_cost_usd
        reranker_cost = None if raw_reranker_cost is None else float(raw_reranker_cost)
    if provider_cost is None or reranker_cost is None:
        return None
    return provider_cost + reranker_cost


def _validate_ablation(
    policy: KnowledgeAblationPolicy,
    reranker: KnowledgeReranker | None,
) -> None:
    if policy.strategy == "cross_encoder" and reranker is None:
        raise ValueError("cross-encoder reranker is required")
    if policy.strategy != "cross_encoder" and reranker is not None:
        raise ValueError("reranker is only valid for the cross_encoder strategy")


def _prepare_reranker(reranker: KnowledgeReranker | None) -> float:
    if reranker is None:
        return 0.0
    started = time.perf_counter()
    prepare = getattr(reranker, "prepare", None)
    if callable(prepare):
        prepare()
    return (time.perf_counter() - started) * 1_000


def _ablation_metadata(
    policy: KnowledgeAblationPolicy,
    prepared: list[tuple[CorpusManifestEntry, PreparedKnowledgeSource]],
    reranker: KnowledgeReranker | None,
) -> dict[str, Any]:
    policy_values = asdict(policy)
    strategy = str(policy_values.pop("strategy"))
    block_lengths = [
        len(block.text.strip())
        for _entry, source in prepared
        for block in source.document.blocks
        if block.kind not in {"frontmatter", "heading"} and block.text.strip()
    ]
    return {
        "strategy": strategy,
        "parameters": policy_values,
        "description_provider": (
            {
                "provider_id": ExtractiveParentDescriptionProvider.provider_id,
                "provider_revision": ExtractiveParentDescriptionProvider.provider_revision,
            }
            if strategy == "described_parent_child"
            else None
        ),
        "reranker": (
            {
                "model_id": reranker.model_id,
                "model_revision": reranker.model_revision,
                "estimated_cost_usd": reranker.estimated_cost_usd,
            }
            if reranker is not None
            else None
        ),
        "corpus_profile": {
            "block_count": len(block_lengths),
            "parent_child_eligible_block_count": sum(
                length > policy.parent_child_max_chars for length in block_lengths
            ),
            "semantic_boundary_eligible_block_count": sum(
                length > policy.semantic_min_chars for length in block_lengths
            ),
        },
    }


def _prepare_corpus(
    store: KnowledgeStore,
    repo_root: Path,
    snapshot_root: Path,
    dataset: VersionedKnowledgeDataset,
) -> list[tuple[CorpusManifestEntry, PreparedKnowledgeSource]]:
    prepared: list[tuple[CorpusManifestEntry, PreparedKnowledgeSource]] = []
    for entry in dataset.corpus:
        snapshot = (repo_root / entry.snapshot_path).resolve()
        relative = snapshot.relative_to(snapshot_root).as_posix()
        source = store.prepare_ingest("versioned-corpus", relative)
        if source.source_revision != entry.content_hash:
            raise ValueError(f"prepared corpus revision changed: {entry.corpus_id}")
        prepared.append((entry, source))
    return prepared


def _prepare_provider(
    provider: DenseEmbeddingProvider,
    prepared: list[tuple[CorpusManifestEntry, PreparedKnowledgeSource]],
    cases: tuple[EvalCase, ...],
    *,
    ablation_policy: KnowledgeAblationPolicy | None = None,
) -> None:
    document_texts: list[str] = []
    for entry, source in prepared:
        chunks = chunk_document(
            source.document,
            workspace_id="sage-official-agent-fullstack-v1",
            page_id=entry.corpus_id,
            page_revision=source.source_revision,
            page_path=entry.snapshot_path,
            source_id=source.source_id,
            source_revision=source.source_revision,
            source_kind=source.source_kind,
            source_relative_path=entry.snapshot_path,
            proposal_id="eval-prepare",
            artifact_id=None,
            title=source.document.title or entry.corpus_id,
            visibility="private",
            active=True,
            ablation_policy=ablation_policy,
            semantic_provider=provider,
        )
        document_texts.extend(
            embedding_text(chunk, ablation_policy=ablation_policy) for chunk in chunks
        )
    prepare_document_embeddings(provider, tuple(dict.fromkeys(document_texts)))
    prepare_query_embeddings(provider, tuple(dict.fromkeys(case.query for case in cases)))


def _run_route(
    store: KnowledgeStore,
    dataset: VersionedKnowledgeDataset,
    relative_to_entry: dict[str, CorpusManifestEntry],
    *,
    retrieval_mode: KnowledgeRetrievalMode,
    top_k: int,
    candidate_k: int,
    token_budget: int,
    minimum_answerable_recall: float,
    estimated_cost_usd: float | None,
    gate_threshold: float | None = None,
    semantic_provider: bool = False,
) -> dict[str, Any]:
    raw_cases: list[_RawCase] = []
    for case in dataset.cases:
        started = time.perf_counter()
        try:
            recovery = store.search_with_recovery(
                case.query,
                top_k=candidate_k,
                retrieval_mode=retrieval_mode,
            )
            hits = recovery.hits
            error_type = None
        except Exception as exc:  # report exception class without leaking provider details
            hits = ()
            recovery = None
            error_type = type(exc).__name__
        raw_cases.append(
            _RawCase(
                case=case,
                hits=hits,
                latency_ms=(time.perf_counter() - started) * 1_000,
                error_type=error_type,
                recovery=recovery,
            )
        )

    calibration_rows = [item for item in raw_cases if item.case.dataset_split == "calibration"]
    observations = tuple(
        GateObservation(
            item.case.case_id,
            item.case.answerable,
            _route_score(item.hits, retrieval_mode),
        )
        for item in calibration_rows
    )
    if gate_threshold is None:
        calibration = calibrate_gate(
            observations,
            minimum_answerable_recall=minimum_answerable_recall,
        )
        threshold_source = "calibrated_current_run"
    else:
        metrics = _gate_metrics(observations, gate_threshold)
        calibration = GateCalibration(
            threshold=gate_threshold,
            minimum_answerable_recall=minimum_answerable_recall,
            target_met=float(metrics["answerable_recall"]) >= minimum_answerable_recall,
            case_count=len(observations),
            answerable_count=sum(item.answerable for item in observations),
            unanswerable_count=sum(not item.answerable for item in observations),
            metrics=metrics,
        )
        threshold_source = "fixed_policy"
    cases = [
        _evaluate_case(
            store,
            item,
            relative_to_entry,
            retrieval_mode=retrieval_mode,
            gate_threshold=calibration.threshold,
            top_k=top_k,
            token_budget=token_budget,
        )
        for item in raw_cases
    ]
    summary = _summarize_cases(cases)
    latencies = [item.latency_ms for item in raw_cases]
    return {
        "label": {
            "sparse": "SQLite FTS5 sparse-only",
            "dense": (
                "Semantic dense-only"
                if semantic_provider
                else "Hashing dense-only; deterministic and non-semantic"
            ),
            "hybrid": (
                "SQLite FTS5 + semantic dense + RRF"
                if semantic_provider
                else "SQLite FTS5 + constrained Hashing + RRF"
            ),
        }[retrieval_mode],
        "retrieval": summary["retrieval"],
        "ranking": summary["ranking"],
        "gate": {
            "calibration_split": "calibration",
            "threshold_source": threshold_source,
            **asdict(calibration),
            "evaluation": summary["gate"],
        },
        "generation": {
            "evaluator": "deterministic_extractive_proxy",
            "llm_judge_used": False,
            "claim_metric_interpretation": (
                "cross-language extractive completeness lower bound; not a pass threshold"
            ),
            **summary["generation"],
        },
        "citation": summary["citation"],
        "system": {
            **summary["system"],
            "latency_ms": {
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
            },
            "estimated_cost_usd": estimated_cost_usd,
        },
        "failures": summary["failures"],
        "splits": {
            split: _summarize_cases([item for item in cases if item["dataset_split"] == split])
            for split in ("dev", "calibration", "test")
        },
        "categories": {
            category: _summarize_cases([item for item in cases if item["category"] == category])
            for category in sorted({case.category for case in dataset.cases})
        },
        "cases": cases,
    }


def _evaluate_case(
    store: KnowledgeStore,
    raw: _RawCase,
    relative_to_entry: dict[str, CorpusManifestEntry],
    *,
    retrieval_mode: KnowledgeRetrievalMode,
    gate_threshold: float,
    top_k: int,
    token_budget: int,
) -> dict[str, Any]:
    case = raw.case
    candidate_documents = _ranked_passages(raw.hits, relative_to_entry)
    top_hits = raw.hits[:top_k]
    top_documents = _ranked_passages(top_hits, relative_to_entry)
    relevance = {
        f"{item.corpus_id}#{item.anchor}": item.relevance for item in case.required_passages
    }
    matched_top = tuple(item for item in top_documents if item in relevance)
    matched_candidates = tuple(item for item in candidate_documents if item in relevance)
    retrieved_sources = {
        relative_to_entry[hit.chunk.source_relative_path].corpus_id for hit in top_hits
    }
    score = _route_score(raw.hits, retrieval_mode)
    accepted = raw.error_type is None and score is not None and score >= gate_threshold
    bundle = assemble_retrieval_bundle(
        case.query,
        top_hits if accepted else (),
        token_budget=token_budget,
    )
    context_documents = _ranked_passages(
        tuple(item.hit for item in bundle.evidence), relative_to_entry
    )
    answer = "\n\n".join(item.excerpt for item in bundle.evidence)
    claim_recalls = [_claim_token_recall(claim, answer) for claim in case.required_claims]
    required_claim_recall = sum(claim_recalls) / len(claim_recalls) if claim_recalls else None
    normalized_answer = _normalize_text(answer)
    forbidden_matches = sum(
        bool(_normalize_text(claim)) and _normalize_text(claim) in normalized_answer
        for claim in case.forbidden_claims
    )
    citation_valid_count = 0
    version_valid_count = 0
    for evidence in bundle.evidence:
        try:
            resolved = store.citation(evidence.hit.citation_id)
        except (KeyError, ValueError):
            continue
        if resolved.chunk_id == evidence.hit.chunk.chunk_id:
            citation_valid_count += 1
        entry = relative_to_entry[evidence.hit.chunk.source_relative_path]
        if resolved.source_revision == entry.content_hash:
            version_valid_count += 1
    evidence_count = len(bundle.evidence)
    first_rank = next(
        (rank for rank, item in enumerate(top_documents, start=1) if item in relevance),
        None,
    )
    ndcg = _ndcg(top_documents, relevance, top_k)
    primary_failure = _primary_failure(
        case,
        raw.error_type,
        candidate_match=bool(matched_candidates),
        top_match=bool(matched_top),
        accepted=accepted,
        context_match=any(item in relevance for item in context_documents),
        forbidden_matches=forbidden_matches,
        citation_valid=evidence_count == citation_valid_count,
    )
    trace = {
        "failure_type": primary_failure,
        "ingestion": {"dataset_loaded": raw.error_type is None},
        "retrieval": {
            "candidate_count": len(raw.hits),
            "matched_candidate_count": len(matched_candidates),
            "top_k_count": len(top_hits),
            "matched_top_k_count": len(matched_top),
        },
        "ranking": {"relevant_first_rank": first_rank},
        "context": {
            "evidence_count": evidence_count,
            "omitted_count": bundle.omitted_count,
            "matched_evidence_count": len(
                {item for item in context_documents if item in relevance}
            ),
        },
        "gate": {"accepted": accepted, "score_available": score is not None},
        "generation": {
            "forbidden_match_count": forbidden_matches,
            "extractive_grounded": forbidden_matches == 0,
        },
        "citation": {
            "valid_count": citation_valid_count,
            "evidence_count": evidence_count,
        },
        "system": {"error_type": raw.error_type},
        "recovery": (
            {
                "status": raw.recovery.status,
                "round_count": raw.recovery.round_count,
                "no_evidence_reason": raw.recovery.no_evidence_reason,
                "attempts": [asdict(attempt) for attempt in raw.recovery.attempts],
            }
            if raw.recovery is not None
            else {
                "status": "exhausted",
                "round_count": 0,
                "no_evidence_reason": "bounded_recovery_exhausted",
                "attempts": [],
            }
        ),
    }
    return {
        "case_id": case.case_id,
        "query": case.query,
        "dataset_split": case.dataset_split,
        "category": case.category,
        "answerable": case.answerable,
        "primary_failure": primary_failure,
        "trace": trace,
        "retrieval": {
            "recall_at_k": len(matched_top) / len(relevance) if relevance else 0.0,
            "candidate_recall": len(matched_candidates) / len(relevance) if relevance else 0.0,
            "source_recall_at_k": (
                len(retrieved_sources.intersection(case.required_sources))
                / len(case.required_sources)
                if case.required_sources
                else 0.0
            ),
            "hit": bool(matched_top),
        },
        "ranking": {
            "reciprocal_rank": 1.0 / first_rank if first_rank is not None else 0.0,
            "ndcg_at_k": ndcg,
        },
        "gate": {"score": score, "accepted": accepted},
        "context": {
            "used_tokens": bundle.used_tokens,
            "evidence_count": evidence_count,
            "gold_passage_recall": (
                len({item for item in context_documents if item in relevance}) / len(relevance)
                if relevance
                else 0.0
            ),
        },
        "generation": {
            "required_claim_token_recall": required_claim_recall,
            "forbidden_claim_exact_matches": forbidden_matches,
            "extractive_grounded": bool(bundle.evidence) if accepted else True,
        },
        "citation": {
            "evidence_count": evidence_count,
            "valid_count": citation_valid_count,
            "version_valid_count": version_valid_count,
            "gold_passage_recall": (
                len({item for item in context_documents if item in relevance}) / len(relevance)
                if relevance
                else 0.0
            ),
        },
        "system": {"latency_ms": round(raw.latency_ms, 3), "error_type": raw.error_type},
        "hits": [
            {
                "passage_id": _hit_passage(hit, relative_to_entry),
                "rank": hit.rank,
                "citation_id": hit.citation_id,
                "rrf_score": hit.rrf_score,
                "sparse_rank": hit.sparse_rank,
                "sparse_score": hit.sparse_score,
                "dense_rank": hit.dense_rank,
                "dense_score": hit.dense_score,
                "rerank_score": hit.rerank_score,
            }
            for hit in top_hits
        ],
    }


def _primary_failure(
    case: EvalCase,
    error_type: str | None,
    *,
    candidate_match: bool,
    top_match: bool,
    accepted: bool,
    context_match: bool,
    forbidden_matches: int,
    citation_valid: bool,
) -> str:
    if error_type is not None:
        return "system"
    if not case.answerable:
        return "false_acceptance" if accepted else "none"
    if not candidate_match:
        return "retrieval"
    if not top_match:
        return "ranking"
    if not accepted:
        return "false_rejection"
    if not context_match:
        return "context"
    if not citation_valid:
        return "citation"
    if forbidden_matches:
        return "grounding"
    return "none"


def _summarize_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    answerable = [item for item in cases if item["answerable"]]
    accepted_answerable = [item for item in answerable if bool(item["gate"]["accepted"])]
    accepted = [item for item in cases if bool(item["gate"]["accepted"])]
    observations = tuple(
        GateObservation(
            str(item["case_id"]),
            bool(item["answerable"]),
            1.0 if bool(item["gate"]["accepted"]) else None,
        )
        for item in cases
    )
    gate = _gate_metrics(observations, 1.0) if cases else _empty_gate_metrics()
    evidence_count = sum(int(item["citation"]["evidence_count"]) for item in cases)
    valid_count = sum(int(item["citation"]["valid_count"]) for item in cases)
    version_valid_count = sum(int(item["citation"]["version_valid_count"]) for item in cases)
    forbidden_matches = sum(
        int(item["generation"]["forbidden_claim_exact_matches"]) for item in accepted
    )
    return {
        "case_count": len(cases),
        "retrieval": {
            "recall_at_k": _average(answerable, "retrieval", "recall_at_k"),
            "candidate_recall": _average(answerable, "retrieval", "candidate_recall"),
            "source_recall_at_k": _average(answerable, "retrieval", "source_recall_at_k"),
            "hit_rate": _average(answerable, "retrieval", "hit"),
        },
        "ranking": {
            "mrr": _average(answerable, "ranking", "reciprocal_rank"),
            "ndcg_at_k": _average(answerable, "ranking", "ndcg_at_k"),
        },
        "gate": gate,
        "generation": {
            "required_claim_token_recall": _average(
                answerable,
                "generation",
                "required_claim_token_recall",
                missing=0.0,
            ),
            "accepted_required_claim_token_recall": _average(
                accepted_answerable,
                "generation",
                "required_claim_token_recall",
                missing=0.0,
            ),
            "forbidden_claim_exact_match_count": forbidden_matches,
            "extractive_grounding_rate": _average(accepted, "generation", "extractive_grounded"),
        },
        "citation": {
            "evidence_count": evidence_count,
            "support_rate": valid_count / evidence_count if evidence_count else 0.0,
            "error_rate": (evidence_count - valid_count) / evidence_count
            if evidence_count
            else 0.0,
            "version_validity_rate": (
                version_valid_count / evidence_count if evidence_count else 0.0
            ),
            "gold_passage_recall": _average(answerable, "citation", "gold_passage_recall"),
        },
        "system": {
            "case_count": len(cases),
            "error_count": sum(item["system"]["error_type"] is not None for item in cases),
            "average_context_tokens": _average(cases, "context", "used_tokens"),
        },
        "failures": {
            layer: sum(item["primary_failure"] == layer for item in cases)
            for layer in _FAILURE_LAYERS
        },
    }


def _gate_metrics(
    observations: Sequence[GateObservation], threshold: float
) -> dict[str, float | int]:
    predicted = [item.score is not None and item.score >= threshold for item in observations]
    true_accept = sum(
        item.answerable and decision for item, decision in zip(observations, predicted, strict=True)
    )
    false_accept = sum(
        not item.answerable and decision
        for item, decision in zip(observations, predicted, strict=True)
    )
    true_reject = sum(
        not item.answerable and not decision
        for item, decision in zip(observations, predicted, strict=True)
    )
    false_reject = sum(
        item.answerable and not decision
        for item, decision in zip(observations, predicted, strict=True)
    )
    answerable_precision = _ratio(true_accept, true_accept + false_accept)
    answerable_recall = _ratio(true_accept, true_accept + false_reject)
    abstain_precision = _ratio(true_reject, true_reject + false_reject)
    abstain_recall = _ratio(true_reject, true_reject + false_accept)
    return {
        "true_accept": true_accept,
        "false_accept": false_accept,
        "true_reject": true_reject,
        "false_reject": false_reject,
        "accuracy": _ratio(true_accept + true_reject, len(observations)),
        "answerable_precision": answerable_precision,
        "answerable_recall": answerable_recall,
        "answerable_f1": _f1(answerable_precision, answerable_recall),
        "abstain_precision": abstain_precision,
        "abstain_recall": abstain_recall,
        "abstain_f1": _f1(abstain_precision, abstain_recall),
    }


def _empty_gate_metrics() -> dict[str, float | int]:
    return {
        "true_accept": 0,
        "false_accept": 0,
        "true_reject": 0,
        "false_reject": 0,
        "accuracy": 0.0,
        "answerable_precision": 0.0,
        "answerable_recall": 0.0,
        "answerable_f1": 0.0,
        "abstain_precision": 0.0,
        "abstain_recall": 0.0,
        "abstain_f1": 0.0,
    }


def _ranked_passages(
    hits: tuple[KnowledgeSearchHit, ...],
    relative_to_entry: dict[str, CorpusManifestEntry],
) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_hit_passage(hit, relative_to_entry) for hit in hits))


def _hit_passage(hit: KnowledgeSearchHit, relative_to_entry: dict[str, CorpusManifestEntry]) -> str:
    entry = relative_to_entry[hit.chunk.source_relative_path]
    anchor = hit.chunk.heading_path[-1] if hit.chunk.heading_path else hit.chunk.title
    return f"{entry.corpus_id}#{anchor}"


def _route_score(
    hits: tuple[KnowledgeSearchHit, ...], retrieval_mode: KnowledgeRetrievalMode
) -> float | None:
    if not hits:
        return None
    first = hits[0]
    if retrieval_mode == "sparse":
        return first.sparse_score
    if retrieval_mode == "dense":
        return first.dense_score
    if first.rerank_score is not None:
        return first.rerank_score
    return first.rrf_score


def _ndcg(documents: tuple[str, ...], relevance: dict[str, int], top_k: int) -> float:
    dcg = sum(
        ((2 ** relevance[item]) - 1) / math.log2(rank + 1)
        for rank, item in enumerate(documents[:top_k], start=1)
        if item in relevance
    )
    ideal = sorted(relevance.values(), reverse=True)[:top_k]
    ideal_dcg = sum(
        ((2**grade) - 1) / math.log2(rank + 1) for rank, grade in enumerate(ideal, start=1)
    )
    return dcg / ideal_dcg if ideal_dcg else 0.0


def _claim_token_recall(claim: str, evidence: str) -> float:
    required = set(lexical_terms(claim))
    if not required:
        return 0.0
    available = set(lexical_terms(evidence))
    return len(required.intersection(available)) / len(required)


def _normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _average(
    rows: list[dict[str, Any]],
    section: str,
    field: str,
    *,
    missing: float | None = None,
) -> float:
    values: list[float] = []
    for row in rows:
        value = row[section][field]
        if value is None:
            if missing is None:
                continue
            value = missing
        values.append(float(value))
    return sum(values) / len(values) if values else 0.0


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return round(ordered[index], 3)


def _deterministic_digest(report: dict[str, Any]) -> str:
    routes: dict[str, Any] = {}
    for mode, route in report["routes"].items():
        routes[mode] = {
            "retrieval": route["retrieval"],
            "ranking": route["ranking"],
            "gate": route["gate"],
            "generation": route["generation"],
            "citation": route["citation"],
            "failures": route["failures"],
            "cases": [
                {
                    key: (
                        [
                            {
                                hit_key: hit_value
                                for hit_key, hit_value in hit.items()
                                if hit_key != "citation_id"
                            }
                            for hit in value
                        ]
                        if key == "hits"
                        else value
                    )
                    for key, value in case.items()
                    if key not in {"system"}
                }
                for case in route["cases"]
            ],
        }
    payload = {
        "schema_version": report["schema_version"],
        "evaluation_id": report["evaluation_id"],
        "dataset": report["dataset"],
        "inputs": report["inputs"],
        "backend": report["backend"],
        "provider": report["provider"],
        "parameters": report["parameters"],
        "index": report["index"],
        "routes": routes,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _git_value(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


__all__ = [
    "EvalSplit",
    "GateCalibration",
    "GateObservation",
    "calibrate_gate",
    "compare_layered_reports",
    "compare_semantic_provider_reports",
    "run_postgres_layered_eval",
    "run_sqlite_layered_eval",
]
