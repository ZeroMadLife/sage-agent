"""Relation-aware retrieval benchmark with explicit gold graph paths."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from core.knowledge.benchmark_runner import BenchmarkManifest
from core.knowledge.index import LocalKnowledgeIndex
from core.knowledge.relevance import KnowledgeRelevancePolicy
from core.knowledge.retrieval import DenseEmbeddingProvider
from core.knowledge.store import KnowledgeSourceRoot, KnowledgeStore

_RELATION_KINDS = {"chapter_next", "reading_map", "unanswerable"}
_SPLITS = {"dev", "test"}


@dataclass(frozen=True, slots=True)
class RelationGoldPath:
    source: str
    target: str
    relation: str


@dataclass(frozen=True, slots=True)
class RelationBenchmarkQuery:
    query_id: str
    query: str
    split: str
    relation_kind: str
    answerable: bool
    seed_sources: tuple[str, ...]
    required_sources: tuple[str, ...]
    gold_paths: tuple[RelationGoldPath, ...]


@dataclass(frozen=True, slots=True)
class RelationBenchmarkCase:
    query_id: str
    split: str
    relation_kind: str
    answerable: bool
    baseline_sources: tuple[str, ...]
    expanded_sources: tuple[str, ...]
    graph_paths: tuple[RelationGoldPath, ...]
    baseline_all_recall: bool
    expanded_all_recall: bool
    path_recall: float
    path_precision: float
    correct_no_answer: bool


def load_relation_benchmark(path: Path) -> tuple[RelationBenchmarkQuery, ...]:
    queries: list[RelationBenchmarkQuery] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw: Any = json.loads(line)
            queries.append(_query(raw))
        except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
            raise ValueError(f"invalid relation benchmark record at line {line_number}") from exc
    ids = [query.query_id for query in queries]
    if not queries or len(ids) != len(set(ids)):
        raise ValueError("relation benchmark requires unique queries")
    return tuple(queries)


def run_relation_benchmark(
    repo_root: Path,
    manifest: BenchmarkManifest,
    queries: tuple[RelationBenchmarkQuery, ...],
    *,
    provider: DenseEmbeddingProvider,
    relevance_policy: KnowledgeRelevancePolicy,
    dataset_path: Path,
    top_k: int = 10,
) -> dict[str, object]:
    if top_k < 1 or top_k > 50:
        raise ValueError("relation benchmark top_k must be between 1 and 50")
    relevance_policy.assert_provider(
        model_id=provider.model_id, model_revision=provider.model_revision
    )
    corpus_root = _inside(repo_root, manifest.corpus_root)
    with tempfile.TemporaryDirectory(prefix="sage-rag-relation-") as temp:
        temporary = Path(temp)
        store = KnowledgeStore(
            temporary / "workspace",
            temporary / "knowledge.sqlite3",
            {
                "benchmark": KnowledgeSourceRoot(
                    root_id="benchmark",
                    kind="markdown",
                    label="Sage Relation Benchmark",
                    path=corpus_root,
                )
            },
            knowledge_index=LocalKnowledgeIndex(
                workspace_id="sage-knowledge-relation-v1",
                embedding_provider=provider,
                relevance_policy=relevance_policy,
            ),
        )
        for item in manifest.files:
            proposal = store.ingest("benchmark", item.path)
            store.approve(proposal.proposal_id, proposal.revision)

        available = {item.path for item in manifest.files}
        _validate_sources(queries, available)
        cases: list[RelationBenchmarkCase] = []
        latencies: list[float] = []
        for query in queries:
            baseline = store.search(query.query, top_k=top_k)
            started = time.perf_counter()
            expanded = store.search(query.query, top_k=top_k, relation_expand=True)
            latencies.append((time.perf_counter() - started) * 1_000)
            baseline_sources = tuple(
                dict.fromkeys(hit.chunk.source_relative_path for hit in baseline)
            )
            expanded_sources = tuple(
                dict.fromkeys(hit.chunk.source_relative_path for hit in expanded)
            )
            relation_paths = store.expand_relations(
                query.query,
                tuple(dict.fromkeys(hit.chunk.page_id for hit in baseline[:2])),
                limit=min(4, top_k),
            )
            graph_paths = tuple(
                RelationGoldPath(
                    source=path.seed_source_relative_path,
                    target=path.target_source_relative_path,
                    relation="WIKILINK",
                )
                for path in relation_paths
            )
            cases.append(_evaluate_case(query, baseline_sources, expanded_sources, graph_paths))

    answerable = [case for case in cases if case.answerable]
    unanswerable = [case for case in cases if not case.answerable]
    baseline_all_recall = _average_bool(case.baseline_all_recall for case in answerable)
    expanded_all_recall = _average_bool(case.expanded_all_recall for case in answerable)
    return {
        "benchmark_id": "sage-knowledge-relation-v1",
        "benchmark_revision": "sha256:" + _sha256(dataset_path),
        "dataset": dataset_path.relative_to(repo_root).as_posix(),
        "dataset_sha256": _sha256(dataset_path),
        "corpus_benchmark_revision": manifest.benchmark_revision,
        "source_commit": _git_value(repo_root, "rev-parse", "HEAD"),
        "source_dirty": bool(_git_value(repo_root, "status", "--porcelain")),
        "provider": {
            "model_id": provider.model_id,
            "model_revision": provider.model_revision,
            "supports_semantic_recall": provider.supports_semantic_recall,
        },
        "relevance_policy_id": relevance_policy.policy_id,
        "query_count": len(cases),
        "answerable_query_count": len(answerable),
        "unanswerable_query_count": len(unanswerable),
        "top_k": top_k,
        "baseline_all_recall_at_k": baseline_all_recall,
        "graph_all_recall_at_k": expanded_all_recall,
        "graph_gain": expanded_all_recall - baseline_all_recall,
        "graph_path_recall": _average(case.path_recall for case in answerable),
        "graph_path_precision": _weighted_path_precision(answerable),
        "unanswerable_accuracy": _average_bool(case.correct_no_answer for case in unanswerable),
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "cases": [asdict(case) for case in cases],
    }


def _evaluate_case(
    query: RelationBenchmarkQuery,
    baseline_sources: tuple[str, ...],
    expanded_sources: tuple[str, ...],
    graph_paths: tuple[RelationGoldPath, ...],
) -> RelationBenchmarkCase:
    required = set(query.required_sources)
    gold = {(path.source, path.target, path.relation.upper()) for path in query.gold_paths}
    actual = {(path.source, path.target, path.relation.upper()) for path in graph_paths}
    matched = gold.intersection(actual)
    return RelationBenchmarkCase(
        query_id=query.query_id,
        split=query.split,
        relation_kind=query.relation_kind,
        answerable=query.answerable,
        baseline_sources=baseline_sources,
        expanded_sources=expanded_sources,
        graph_paths=graph_paths,
        baseline_all_recall=bool(required) and required.issubset(baseline_sources),
        expanded_all_recall=bool(required) and required.issubset(expanded_sources),
        path_recall=(len(matched) / len(gold) if gold else 0.0),
        path_precision=(len(matched) / len(actual) if actual else 0.0),
        correct_no_answer=not query.answerable and not expanded_sources,
    )


def _query(raw: Any) -> RelationBenchmarkQuery:
    expected = {
        "id",
        "query",
        "split",
        "relation_kind",
        "answerable",
        "seed_sources",
        "required_sources",
        "gold_paths",
    }
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("relation benchmark fields do not match the contract")
    if raw["split"] not in _SPLITS or raw["relation_kind"] not in _RELATION_KINDS:
        raise ValueError("unsupported relation benchmark slice")
    if not str(raw["id"]).strip() or not str(raw["query"]).strip():
        raise ValueError("relation benchmark query id and text are required")
    if not isinstance(raw["answerable"], bool):
        raise TypeError("relation benchmark answerable must be boolean")
    paths = raw["gold_paths"]
    if not isinstance(paths, list):
        raise TypeError("relation benchmark gold paths must be a list")
    gold_paths = tuple(
        RelationGoldPath(
            source=str(path["source"]),
            target=str(path["target"]),
            relation=str(path["relation"]),
        )
        for path in paths
        if isinstance(path, dict) and set(path) == {"source", "target", "relation"}
    )
    if len(gold_paths) != len(paths):
        raise ValueError("invalid relation benchmark gold path")
    if any(
        not path.source.strip() or not path.target.strip() or path.relation.upper() != "WIKILINK"
        for path in gold_paths
    ):
        raise ValueError("relation benchmark gold path is invalid")
    seed_sources = _string_tuple(raw["seed_sources"])
    required_sources = _string_tuple(raw["required_sources"])
    if raw["answerable"] and (not seed_sources or not required_sources or not gold_paths):
        raise ValueError("answerable relation query requires seeds, targets, and gold paths")
    if not raw["answerable"] and (seed_sources or required_sources or gold_paths):
        raise ValueError("unanswerable relation query cannot contain judgments")
    return RelationBenchmarkQuery(
        query_id=str(raw["id"]),
        query=str(raw["query"]),
        split=str(raw["split"]),
        relation_kind=str(raw["relation_kind"]),
        answerable=raw["answerable"],
        seed_sources=seed_sources,
        required_sources=required_sources,
        gold_paths=gold_paths,
    )


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise TypeError("relation benchmark source judgments must be non-empty strings")
    result = tuple(value)
    if len(result) != len(set(result)):
        raise ValueError("relation benchmark source judgments must be unique")
    return result


def _validate_sources(queries: tuple[RelationBenchmarkQuery, ...], available: set[str]) -> None:
    judged = {
        source for query in queries for source in (*query.seed_sources, *query.required_sources)
    }
    judged.update(path.source for query in queries for path in query.gold_paths)
    judged.update(path.target for query in queries for path in query.gold_paths)
    missing = judged - available
    if missing:
        raise ValueError("relation benchmark source is missing from the corpus")


def _inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root.resolve() not in path.parents:
        raise ValueError("relation benchmark path escapes repository root")
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_value(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def _average(values: Any) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _average_bool(values: Any) -> float:
    return _average(float(value) for value in values)


def _weighted_path_precision(cases: list[RelationBenchmarkCase]) -> float:
    actual = sum(len(case.graph_paths) for case in cases)
    if not actual:
        return 0.0
    matched = sum(case.path_precision * len(case.graph_paths) for case in cases)
    return matched / actual


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * ratio)))
    return ordered[position]
