"""Reproducible Benchmark v2 execution against the public KnowledgeStore seam."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from core.knowledge.benchmark import (
    KnowledgeBenchmarkQueryV2,
    evaluate_retrieval_v2,
    load_benchmark_v2,
    passage_id,
)
from core.knowledge.index import LocalKnowledgeIndex
from core.knowledge.index_backend import KnowledgeIndexBackend
from core.knowledge.retrieval import (
    DenseEmbeddingProvider,
    HashingEmbeddingProvider,
    KnowledgeAblationPolicy,
    chunk_document,
    embedding_text,
)
from core.knowledge.store import KnowledgeSourceRoot, KnowledgeStore, PreparedKnowledgeSource

BenchmarkIndexFactory = Callable[
    [str, DenseEmbeddingProvider, KnowledgeAblationPolicy],
    KnowledgeIndexBackend,
]


@dataclass(frozen=True, slots=True)
class BenchmarkCorpusFile:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    benchmark_id: str
    benchmark_revision: str
    dataset: str
    dataset_sha256: str
    corpus_root: str
    files: tuple[BenchmarkCorpusFile, ...]


def load_manifest(repo_root: Path, path: Path) -> BenchmarkManifest:
    manifest = read_manifest(path)
    validate_manifest(repo_root, manifest)
    return manifest


def read_manifest(path: Path) -> BenchmarkManifest:
    """Read and validate a manifest contract without requiring the corpus cache."""

    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {
        "benchmark_id",
        "benchmark_revision",
        "dataset",
        "dataset_sha256",
        "corpus_root",
        "files",
    }:
        raise ValueError("invalid benchmark manifest")
    files = raw["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("benchmark manifest requires corpus files")
    parsed_files: list[BenchmarkCorpusFile] = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("invalid benchmark corpus file")
        relative = str(item["path"])
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("benchmark corpus path must be relative")
        digest = str(item["sha256"])
        if len(digest) != 64:
            raise ValueError("invalid benchmark corpus digest")
        parsed_files.append(BenchmarkCorpusFile(relative, digest))
    return BenchmarkManifest(
        benchmark_id=str(raw["benchmark_id"]),
        benchmark_revision=str(raw["benchmark_revision"]),
        dataset=str(raw["dataset"]),
        dataset_sha256=str(raw["dataset_sha256"]),
        corpus_root=str(raw["corpus_root"]),
        files=tuple(parsed_files),
    )


def validate_manifest(repo_root: Path, manifest: BenchmarkManifest) -> None:
    dataset = _inside(repo_root, manifest.dataset)
    if _sha256(dataset) != manifest.dataset_sha256:
        raise ValueError("benchmark dataset revision does not match manifest")
    corpus_root = _inside(repo_root, manifest.corpus_root)
    expected = {item.path for item in manifest.files}
    # The frozen Benchmark v2 corpus is Markdown today, while the book
    # benchmark deliberately exercises the same runner with UTF-8 TXT.
    # Compare the explicit allowlist against all regular files so a new
    # parser-backed corpus cannot silently bypass manifest validation.
    actual = {
        path.relative_to(corpus_root).as_posix()
        for path in corpus_root.rglob("*")
        if path.is_file()
    }
    missing = expected - actual
    if missing:
        raise ValueError("benchmark corpus files are missing from corpus root")
    for item in manifest.files:
        if _sha256(_inside(corpus_root, item.path)) != item.sha256:
            raise ValueError(f"benchmark corpus revision changed: {item.path}")


def load_embedding_provider(factory: str | None) -> DenseEmbeddingProvider:
    if not factory:
        return HashingEmbeddingProvider()
    module_name, separator, attribute = factory.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("provider factory must use module:attribute")
    value = getattr(importlib.import_module(module_name), attribute)
    provider = value() if callable(value) else value
    for name in (
        "model_id",
        "model_revision",
        "dimensions",
        "supports_semantic_recall",
        "embed",
    ):
        if not hasattr(provider, name):
            raise TypeError(f"embedding provider is missing {name}")
    return cast(DenseEmbeddingProvider, provider)


def run_benchmark(
    repo_root: Path,
    manifest: BenchmarkManifest,
    *,
    top_k: int = 10,
    provider: DenseEmbeddingProvider | None = None,
    ablation_policy: KnowledgeAblationPolicy | None = None,
    index_factory: BenchmarkIndexFactory | None = None,
    workspace_id: str = "sage-knowledge-benchmark-v2",
    retrieval_mode: str = "hybrid",
) -> dict[str, Any]:
    if top_k < 1 or top_k > 50:
        raise ValueError("benchmark top_k must be between 1 and 50")
    if retrieval_mode not in {"sparse", "dense", "hybrid"}:
        raise ValueError("invalid benchmark retrieval mode")
    queries = load_benchmark_v2(_inside(repo_root, manifest.dataset))
    embedding_provider = provider or HashingEmbeddingProvider()
    policy = ablation_policy or KnowledgeAblationPolicy()
    with tempfile.TemporaryDirectory(prefix="sage-rag-v2-") as temp:
        temporary = Path(temp)
        store, chunking, available_passages = build_benchmark_store(
            repo_root,
            manifest,
            workspace_path=temporary / "workspace",
            database_path=temporary / "knowledge.sqlite3",
            embedding_provider=embedding_provider,
            ablation_policy=policy,
            query_texts=tuple(query.query for query in queries),
            index_factory=index_factory,
            workspace_id=workspace_id,
        )
        try:
            return _evaluate_benchmark_store(
                repo_root=repo_root,
                manifest=manifest,
                store=store,
                queries=queries,
                available_passages=available_passages,
                chunking=chunking,
                embedding_provider=embedding_provider,
                policy=policy,
                top_k=top_k,
                retrieval_mode=retrieval_mode,
            )
        finally:
            active_error = sys.exc_info()[0] is not None
            try:
                cleanup_benchmark_store(store)
            except Exception:
                if not active_error:
                    raise


def _evaluate_benchmark_store(
    *,
    repo_root: Path,
    manifest: BenchmarkManifest,
    store: KnowledgeStore,
    queries: tuple[KnowledgeBenchmarkQueryV2, ...],
    available_passages: set[str],
    chunking: dict[str, int | float | bool],
    embedding_provider: DenseEmbeddingProvider,
    policy: KnowledgeAblationPolicy,
    top_k: int,
    retrieval_mode: str,
) -> dict[str, Any]:
    _validate_judgments(queries, available_passages)

    ranked: dict[str, tuple[str, ...]] = {}
    latencies: list[float] = []
    latency_by_query: dict[str, float] = {}
    raw_hits: dict[str, list[dict[str, Any]]] = {}
    for query in queries:
        started = time.perf_counter()
        hits = store.search(
            query.query,
            top_k=top_k,
            retrieval_mode=cast(Any, retrieval_mode),
        )
        elapsed_ms = (time.perf_counter() - started) * 1_000
        latencies.append(elapsed_ms)
        latency_by_query[query.query_id] = elapsed_ms
        documents = tuple(
            dict.fromkeys(
                passage_id(
                    hit.chunk.source_relative_path,
                    _passage_section(
                        hit.chunk.source_relative_path,
                        hit.chunk.heading_path or (hit.chunk.title,),
                    ),
                )
                for hit in hits
            )
        )
        ranked[query.query_id] = documents
        raw_hits[query.query_id] = [
            {
                "passage_id": passage_id(
                    hit.chunk.source_relative_path,
                    _passage_section(
                        hit.chunk.source_relative_path,
                        hit.chunk.heading_path or (hit.chunk.title,),
                    ),
                ),
                "chunk_id": hit.chunk.chunk_id,
                "citation_id": hit.citation_id,
                "rank": hit.rank,
                "rrf_score": hit.rrf_score,
                "sparse_rank": hit.sparse_rank,
                "sparse_score": hit.sparse_score,
                "dense_rank": hit.dense_rank,
                "dense_score": hit.dense_score,
                "route_agreement": hit.sparse_rank is not None and hit.dense_rank is not None,
            }
            for hit in hits
        ]
    report = evaluate_retrieval_v2(queries, ranked, top_k=top_k)
    result = asdict(report)
    for case in result["cases"]:
        case["hits"] = raw_hits[str(case["query_id"])]
        case["latency_ms"] = round(latency_by_query[str(case["query_id"])], 3)
    result.update(
        {
            "benchmark_id": manifest.benchmark_id,
            "benchmark_revision": manifest.benchmark_revision,
            "dataset_sha256": manifest.dataset_sha256,
            "corpus_file_count": len(manifest.files),
            "source_commit": _git_value(repo_root, "rev-parse", "HEAD"),
            "source_dirty": bool(_git_value(repo_root, "status", "--porcelain")),
            "provider": {
                "model_id": embedding_provider.model_id,
                "model_revision": embedding_provider.model_revision,
                "dimensions": embedding_provider.dimensions,
                "supports_semantic_recall": embedding_provider.supports_semantic_recall,
            },
            "retrieval_mode": retrieval_mode,
            "ablation_policy": asdict(policy),
            "chunking": chunking,
            "latency_ms": {
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
            },
            "index": asdict(store.index_summary()),
        }
    )
    return result


def build_benchmark_store(
    repo_root: Path,
    manifest: BenchmarkManifest,
    *,
    workspace_path: Path,
    database_path: Path,
    embedding_provider: DenseEmbeddingProvider,
    ablation_policy: KnowledgeAblationPolicy,
    query_texts: tuple[str, ...] = (),
    index_factory: BenchmarkIndexFactory | None = None,
    workspace_id: str = "sage-knowledge-benchmark-v2",
) -> tuple[KnowledgeStore, dict[str, int | float | bool], set[str]]:
    """Build an approved benchmark store for retrieval and generation evals.

    The caller owns ``workspace_path`` and ``database_path``. This keeps the
    corpus/index lifecycle explicit while allowing the generation evaluator to
    inspect the same citation-bound chunks that retrieval returns.
    """

    corpus_root = _inside(repo_root, manifest.corpus_root)
    factory = index_factory or _local_index_factory
    index = factory(workspace_id, embedding_provider, ablation_policy)
    try:
        return _populate_benchmark_store(
            manifest=manifest,
            corpus_root=corpus_root,
            workspace_path=workspace_path,
            database_path=database_path,
            embedding_provider=embedding_provider,
            ablation_policy=ablation_policy,
            query_texts=query_texts,
            workspace_id=workspace_id,
            index=index,
        )
    except BaseException:
        with suppress(Exception):
            _cleanup_benchmark_index(index)
        raise


def _populate_benchmark_store(
    *,
    manifest: BenchmarkManifest,
    corpus_root: Path,
    workspace_path: Path,
    database_path: Path,
    embedding_provider: DenseEmbeddingProvider,
    ablation_policy: KnowledgeAblationPolicy,
    query_texts: tuple[str, ...],
    workspace_id: str,
    index: KnowledgeIndexBackend,
) -> tuple[KnowledgeStore, dict[str, int | float | bool], set[str]]:
    store = KnowledgeStore(
        workspace_path,
        database_path,
        {
            "benchmark": KnowledgeSourceRoot(
                root_id="benchmark",
                kind="markdown",
                label="Sage Benchmark v2",
                path=corpus_root,
            )
        },
        knowledge_index=index,
        ablation_policy=ablation_policy,
    )
    prepared_sources: list[tuple[BenchmarkCorpusFile, PreparedKnowledgeSource]] = []
    available_passages: set[str] = set()
    for item in manifest.files:
        prepared = store.prepare_ingest("benchmark", item.path)
        prepared_sources.append((item, prepared))
        available_passages.update(
            passage_id(item.path, _passage_section(item.path, block.heading_path))
            for block in prepared.document.blocks
            if block.heading_path
        )
    chunking = _prepare_provider(
        embedding_provider,
        prepared_sources,
        query_texts,
        ablation_policy=ablation_policy,
        workspace_id=workspace_id,
    )
    for _item, prepared in prepared_sources:
        proposal = store.ingest_prepared(prepared)
        store.approve(proposal.proposal_id, proposal.revision)
    return store, chunking, available_passages


def _local_index_factory(
    workspace_id: str,
    provider: DenseEmbeddingProvider,
    policy: KnowledgeAblationPolicy,
) -> KnowledgeIndexBackend:
    return LocalKnowledgeIndex(
        workspace_id=workspace_id,
        embedding_provider=provider,
        ablation_policy=policy,
    )


def _cleanup_benchmark_index(index: KnowledgeIndexBackend) -> None:
    delete_workspace = getattr(index, "delete_workspace", None)
    try:
        if callable(delete_workspace):
            delete_workspace()
    finally:
        close = getattr(index, "close", None)
        if callable(close):
            close()


def cleanup_benchmark_store(store: KnowledgeStore) -> None:
    """Delete an external benchmark projection and close its resources."""

    _cleanup_benchmark_index(store.knowledge_index)


def _validate_judgments(
    queries: tuple[KnowledgeBenchmarkQueryV2, ...], available: set[str]
) -> None:
    missing = sorted(
        {judgment.document_id for query in queries for judgment in query.relevant} - available
    )
    if missing:
        sample = ", ".join(missing[:5])
        raise ValueError(f"benchmark relevance passages do not exist in corpus: {sample}")


def _prepare_provider(
    provider: DenseEmbeddingProvider,
    prepared_sources: list[tuple[BenchmarkCorpusFile, PreparedKnowledgeSource]],
    queries: tuple[str, ...],
    *,
    ablation_policy: KnowledgeAblationPolicy,
    workspace_id: str,
) -> dict[str, int | float | bool]:
    prepare = getattr(provider, "prepare", None)
    document_texts: list[str] = []
    eligible_block_count = 0
    indexed_block_ids: set[tuple[str, str]] = set()
    planned_chunk_count = 0
    capacity_reached_source_count = 0
    for item, prepared in prepared_sources:
        eligible_block_count += sum(
            block.kind not in {"frontmatter", "heading"} and bool(block.text.strip())
            for block in prepared.document.blocks
        )
        chunks = chunk_document(
            prepared.document,
            workspace_id=workspace_id,
            page_id=item.path,
            page_revision=prepared.source_revision,
            page_path=item.path,
            source_id=prepared.source_id,
            source_revision=prepared.source_revision,
            source_kind=prepared.source_kind,
            source_relative_path=item.path,
            proposal_id="benchmark-prepare",
            artifact_id=None,
            title=prepared.document.title or item.path,
            visibility="private",
            active=True,
            ablation_policy=ablation_policy,
            semantic_provider=provider,
        )
        planned_chunk_count += len(chunks)
        capacity_reached_source_count += int(len(chunks) >= ablation_policy.max_chunks_per_revision)
        indexed_block_ids.update((item.path, chunk.block_id) for chunk in chunks)
        document_texts.extend(
            embedding_text(chunk, ablation_policy=ablation_policy) for chunk in chunks
        )
    unique_documents = tuple(dict.fromkeys(document_texts))
    prepare_documents = getattr(provider, "prepare_documents", None)
    if callable(prepare_documents):
        prepare_documents(unique_documents)
    elif callable(prepare):
        prepare(unique_documents)
    prepare_queries = getattr(provider, "prepare_queries", None)
    if callable(prepare_queries):
        prepare_queries(tuple(dict.fromkeys(queries)))
    indexed_block_count = len(indexed_block_ids)
    block_coverage = (
        round(indexed_block_count / eligible_block_count, 6) if eligible_block_count else 1.0
    )
    return {
        "eligible_block_count": eligible_block_count,
        "indexed_block_count": indexed_block_count,
        "block_coverage": block_coverage,
        "planned_chunk_count": planned_chunk_count,
        "capacity_reached_source_count": capacity_reached_source_count,
        "index_truncated": (
            indexed_block_count < eligible_block_count or capacity_reached_source_count > 0
        ),
    }


def _passage_section(source_path: str, heading_path: tuple[str, ...]) -> str:
    if not heading_path:
        raise ValueError("benchmark passage requires a heading path")
    if Path(source_path).suffix.lower() == ".txt":
        return " / ".join(heading_path)
    return heading_path[-1]


def _inside(root: Path, relative: str) -> Path:
    root = root.resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("benchmark path escapes repository root") from exc
    return target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * percentile) - 1)
    return round(ordered[index], 3)


__all__ = [
    "BenchmarkIndexFactory",
    "BenchmarkManifest",
    "build_benchmark_store",
    "cleanup_benchmark_store",
    "load_embedding_provider",
    "load_manifest",
    "run_benchmark",
    "validate_manifest",
]
