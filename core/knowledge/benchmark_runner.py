"""Reproducible Benchmark v2 execution against the public KnowledgeStore seam."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import subprocess
import tempfile
import time
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
from core.knowledge.retrieval import (
    DenseEmbeddingProvider,
    HashingEmbeddingProvider,
    chunk_document,
    embedding_text,
)
from core.knowledge.store import KnowledgeSourceRoot, KnowledgeStore, PreparedKnowledgeSource


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
    manifest = BenchmarkManifest(
        benchmark_id=str(raw["benchmark_id"]),
        benchmark_revision=str(raw["benchmark_revision"]),
        dataset=str(raw["dataset"]),
        dataset_sha256=str(raw["dataset_sha256"]),
        corpus_root=str(raw["corpus_root"]),
        files=tuple(parsed_files),
    )
    validate_manifest(repo_root, manifest)
    return manifest


def validate_manifest(repo_root: Path, manifest: BenchmarkManifest) -> None:
    dataset = _inside(repo_root, manifest.dataset)
    if _sha256(dataset) != manifest.dataset_sha256:
        raise ValueError("benchmark dataset revision does not match manifest")
    corpus_root = _inside(repo_root, manifest.corpus_root)
    expected = {item.path for item in manifest.files}
    actual = {
        path.relative_to(corpus_root).as_posix()
        for path in corpus_root.rglob("*.md")
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
) -> dict[str, Any]:
    if top_k < 1 or top_k > 50:
        raise ValueError("benchmark top_k must be between 1 and 50")
    queries = load_benchmark_v2(_inside(repo_root, manifest.dataset))
    embedding_provider = provider or HashingEmbeddingProvider()
    corpus_root = _inside(repo_root, manifest.corpus_root)
    with tempfile.TemporaryDirectory(prefix="sage-rag-v2-") as temp:
        temporary = Path(temp)
        store = KnowledgeStore(
            temporary / "workspace",
            temporary / "knowledge.sqlite3",
            {
                "benchmark": KnowledgeSourceRoot(
                    root_id="benchmark",
                    kind="markdown",
                    label="Sage Benchmark v2",
                    path=corpus_root,
                )
            },
            knowledge_index=LocalKnowledgeIndex(
                workspace_id="sage-knowledge-benchmark-v2",
                embedding_provider=embedding_provider,
            ),
        )
        available_passages: set[str] = set()
        prepared_sources: list[tuple[BenchmarkCorpusFile, PreparedKnowledgeSource]] = []
        for item in manifest.files:
            prepared = store.prepare_ingest("benchmark", item.path)
            prepared_sources.append((item, prepared))
            available_passages.update(
                passage_id(item.path, block.heading_path[-1])
                for block in prepared.document.blocks
                if block.heading_path
            )
        _prepare_provider(
            embedding_provider,
            prepared_sources,
            tuple(query.query for query in queries),
        )
        for _item, prepared in prepared_sources:
            proposal = store.ingest_prepared(prepared)
            store.approve(proposal.proposal_id, proposal.revision)
        _validate_judgments(queries, available_passages)

        ranked: dict[str, tuple[str, ...]] = {}
        latencies: list[float] = []
        raw_hits: dict[str, list[dict[str, Any]]] = {}
        for query in queries:
            started = time.perf_counter()
            hits = store.search(query.query, top_k=top_k)
            latencies.append((time.perf_counter() - started) * 1_000)
            documents = tuple(
                dict.fromkeys(
                    passage_id(
                        hit.chunk.source_relative_path,
                        hit.chunk.heading_path[-1] if hit.chunk.heading_path else hit.chunk.title,
                    )
                    for hit in hits
                )
            )
            ranked[query.query_id] = documents
            raw_hits[query.query_id] = [
                {
                    "passage_id": passage_id(
                        hit.chunk.source_relative_path,
                        hit.chunk.heading_path[-1] if hit.chunk.heading_path else hit.chunk.title,
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
                "latency_ms": {
                    "p50": _percentile(latencies, 0.50),
                    "p95": _percentile(latencies, 0.95),
                },
                "index": asdict(store.index_summary()),
            }
        )
        return result


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
) -> None:
    prepare = getattr(provider, "prepare", None)
    if not callable(prepare):
        return
    texts: list[str] = []
    for item, prepared in prepared_sources:
        chunks = chunk_document(
            prepared.document,
            workspace_id="sage-knowledge-benchmark-v2",
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
        )
        texts.extend(embedding_text(chunk) for chunk in chunks)
    texts.extend(queries)
    prepare(tuple(dict.fromkeys(texts)))


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
    "BenchmarkManifest",
    "load_embedding_provider",
    "load_manifest",
    "run_benchmark",
    "validate_manifest",
]
