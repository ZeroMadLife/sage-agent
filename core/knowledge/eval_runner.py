"""Layered, reproducible evaluation for the versioned Knowledge dataset."""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import tempfile
import time
import unicodedata
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from core.knowledge.datasets import (
    CorpusManifestEntry,
    EvalCase,
    VersionedKnowledgeDataset,
    load_versioned_dataset,
)
from core.knowledge.index import LocalKnowledgeIndex
from core.knowledge.retrieval import (
    DenseEmbeddingProvider,
    HashingEmbeddingProvider,
    KnowledgeRetrievalMode,
    KnowledgeSearchHit,
    assemble_retrieval_bundle,
    chunk_document,
    embedding_text,
    lexical_terms,
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
) -> dict[str, Any]:
    """Run the same frozen corpus through isolated SQLite retrieval routes."""

    root = repo_root.resolve()
    if not retrieval_modes or len(retrieval_modes) != len(set(retrieval_modes)):
        raise ValueError("retrieval modes must be non-empty and unique")
    if any(mode not in {"sparse", "dense", "hybrid"} for mode in retrieval_modes):
        raise ValueError("unsupported retrieval mode")
    if top_k < 1 or top_k > 50 or candidate_k < top_k or candidate_k > 50:
        raise ValueError("eval requires 1 <= top_k <= candidate_k <= 50")
    if token_budget < 256 or token_budget > 20_000:
        raise ValueError("eval token budget must be between 256 and 20000")

    dataset_file = dataset_path.resolve() if dataset_path.is_absolute() else root / dataset_path
    dataset = load_versioned_dataset(root, dataset_file)
    embedding_provider = provider or HashingEmbeddingProvider()
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
            ),
        )
        prepared = _prepare_corpus(store, root, snapshot_root, dataset)
        _prepare_provider(embedding_provider, prepared, dataset.cases)
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
                estimated_cost_usd=(
                    0.0
                    if embedding_provider.model_id == HashingEmbeddingProvider.model_id
                    else None
                ),
            )
            for mode in retrieval_modes
        }
        index = asdict(store.index_summary())

    result: dict[str, Any] = {
        "schema_version": 1,
        "evaluation_id": "sage-sqlite-layered-baseline-v1",
        "dataset": {
            "dataset_id": dataset.manifest.dataset_id,
            "dataset_revision": dataset.manifest.dataset_revision,
            "case_count": len(dataset.cases),
            "corpus_count": len(dataset.corpus),
            "split_counts": dataset.split_counts,
            "frozen_test": dataset.manifest.frozen_test,
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
        "parameters": {
            "top_k": top_k,
            "candidate_k": candidate_k,
            "token_budget": token_budget,
            "minimum_answerable_recall": minimum_answerable_recall,
            "test_split_used_for_calibration": False,
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
) -> None:
    prepare = getattr(provider, "prepare", None)
    if not callable(prepare):
        return
    texts: list[str] = []
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
        )
        texts.extend(embedding_text(chunk) for chunk in chunks)
    texts.extend(case.query for case in cases)
    prepare(tuple(dict.fromkeys(texts)))


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
) -> dict[str, Any]:
    raw_cases: list[_RawCase] = []
    for case in dataset.cases:
        started = time.perf_counter()
        try:
            hits = store.search(
                case.query,
                top_k=candidate_k,
                retrieval_mode=retrieval_mode,
            )
            error_type = None
        except Exception as exc:  # report exception class without leaking provider details
            hits = ()
            error_type = type(exc).__name__
        raw_cases.append(
            _RawCase(
                case=case,
                hits=hits,
                latency_ms=(time.perf_counter() - started) * 1_000,
                error_type=error_type,
            )
        )

    calibration_rows = [item for item in raw_cases if item.case.dataset_split == "calibration"]
    calibration = calibrate_gate(
        tuple(
            GateObservation(
                item.case.case_id,
                item.case.answerable,
                _route_score(item.hits, retrieval_mode),
            )
            for item in calibration_rows
        ),
        minimum_answerable_recall=minimum_answerable_recall,
    )
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
            "dense": "Hashing dense-only; deterministic and non-semantic",
            "hybrid": "SQLite FTS5 + constrained Hashing + RRF",
        }[retrieval_mode],
        "retrieval": summary["retrieval"],
        "ranking": summary["ranking"],
        "gate": {
            "calibration_split": "calibration",
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
    return {
        "case_id": case.case_id,
        "query": case.query,
        "dataset_split": case.dataset_split,
        "category": case.category,
        "answerable": case.answerable,
        "primary_failure": primary_failure,
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
    "GateCalibration",
    "GateObservation",
    "calibrate_gate",
    "run_sqlite_layered_eval",
]
