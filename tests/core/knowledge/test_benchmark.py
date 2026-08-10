from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from core.knowledge.benchmark import (
    KnowledgeBenchmarkQueryV2,
    KnowledgeGoldenQuery,
    KnowledgeRelevanceJudgment,
    evaluate_retrieval,
    evaluate_retrieval_v2,
    load_benchmark_v2,
)
from core.knowledge.benchmark_runner import (
    BenchmarkCorpusFile,
    BenchmarkManifest,
    load_manifest,
    read_manifest,
    run_benchmark,
)
from core.knowledge.index import LocalKnowledgeIndex
from core.knowledge.retrieval import (
    DenseEmbeddingProvider,
    KnowledgeAblationPolicy,
    KnowledgeRetrievalMode,
    KnowledgeSearchHit,
)


class _RoleAwareBenchmarkProvider:
    model_id = "test.role-aware"
    model_revision = "1"
    dimensions = 2
    supports_semantic_recall = True

    def __init__(self) -> None:
        self.documents: tuple[str, ...] = ()
        self.queries: tuple[str, ...] = ()

    def prepare_documents(self, texts: tuple[str, ...]) -> None:
        self.documents = texts

    def prepare_queries(self, texts: tuple[str, ...]) -> None:
        self.queries = texts

    def embed_document(self, _text: str) -> tuple[float, ...]:
        return (1.0, 0.0)

    def embed_query(self, _text: str) -> tuple[float, ...]:
        return (1.0, 0.0)

    def embed(self, text: str) -> tuple[float, ...]:
        return self.embed_document(text)


class _FailingPrepareProvider(_RoleAwareBenchmarkProvider):
    def prepare_documents(self, texts: tuple[str, ...]) -> None:
        del texts
        raise RuntimeError("simulated provider preparation failure")


class _TrackingBenchmarkIndex(LocalKnowledgeIndex):
    def __init__(
        self,
        *,
        workspace_id: str,
        embedding_provider: DenseEmbeddingProvider,
        ablation_policy: KnowledgeAblationPolicy,
        fail_search: bool = False,
        fail_cleanup: bool = False,
    ) -> None:
        super().__init__(
            workspace_id=workspace_id,
            embedding_provider=embedding_provider,
            ablation_policy=ablation_policy,
        )
        self.fail_search = fail_search
        self.fail_cleanup = fail_cleanup
        self.search_calls = 0
        self.workspace_deleted = False
        self.closed = False

    @property
    def backend_id(self) -> str:
        return "test-tracking-index"

    def search(
        self,
        connection: sqlite3.Connection,
        query: str,
        *,
        top_k: int = 8,
        visibility: str = "private",
        source_ids: tuple[str, ...] = (),
        page_revisions: tuple[str, ...] = (),
        retrieval_mode: KnowledgeRetrievalMode = "hybrid",
        round_index: int = 1,
        trace_query: str | None = None,
        rewrite: str | None = None,
    ) -> tuple[KnowledgeSearchHit, ...]:
        self.search_calls += 1
        if self.fail_search:
            raise RuntimeError("simulated benchmark query failure")
        return super().search(
            connection,
            query,
            top_k=top_k,
            visibility=visibility,
            source_ids=source_ids,
            page_revisions=page_revisions,
            retrieval_mode=retrieval_mode,
            round_index=round_index,
            trace_query=trace_query,
            rewrite=rewrite,
        )

    def delete_workspace(self) -> None:
        self.workspace_deleted = True
        if self.fail_cleanup:
            raise RuntimeError("simulated benchmark cleanup failure")

    def close(self) -> None:
        self.closed = True


def _small_benchmark_fixture(tmp_path: Path) -> BenchmarkManifest:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "book.txt"
    source.write_text(
        "CHAPTER I.\n\n" + "division of labour improves productive power. " * 20,
        encoding="utf-8",
    )
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        '{"id":"q1","query":"Why does division of labour help?",'
        '"category":"real_user","split":"test","answerable":true,'
        '"provenance":"test","relevant_passages":['
        '{"source":"book.txt","section":"CHAPTER I.","relevance":3}],'
        '"required_claims":[],"forbidden_claims":[]}\n',
        encoding="utf-8",
    )
    return BenchmarkManifest(
        benchmark_id="book-test",
        benchmark_revision="1",
        dataset="dataset.jsonl",
        dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
        corpus_root="corpus",
        files=(
            BenchmarkCorpusFile(
                path="book.txt",
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            ),
        ),
    )


def test_retrieval_metrics_use_source_relevance_and_rank() -> None:
    golden = (
        KnowledgeGoldenQuery("q1", "memory", "memory", ("memory.md",)),
        KnowledgeGoldenQuery("q2", "context", "context", ("context.md", "prompt.md")),
    )
    report = evaluate_retrieval(
        golden,
        {
            "q1": ("other.md", "memory.md"),
            "q2": ("context.md", "other.md"),
        },
        top_k=2,
    )

    assert report.query_count == 2
    assert report.recall_at_k == 0.75
    assert report.mrr == 0.75
    assert 0.0 < report.ndcg_at_k < 1.0
    assert report.hit_rate == 1.0


def test_committed_golden_query_set_starts_with_fifty_unique_cases() -> None:
    path = Path(__file__).parents[3] / "evals" / "knowledge_golden_queries.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert len(payload) == 50
    assert len({item["id"] for item in payload}) == 50
    assert all(item["query"].strip() for item in payload)
    assert all(item["category"].strip() for item in payload)
    assert all(item["relevant_sources"] for item in payload)


def test_v2_metrics_support_graded_relevance_and_unanswerable_cases() -> None:
    queries = (
        KnowledgeBenchmarkQueryV2(
            query_id="q-answerable",
            query="How is context bounded?",
            category="real_user",
            split="test",
            answerable=True,
            relevant=(
                KnowledgeRelevanceJudgment("context.md#Artifact", 3),
                KnowledgeRelevanceJudgment("context.md#Budget", 1),
            ),
        ),
        KnowledgeBenchmarkQueryV2(
            query_id="q-no-answer",
            query="What is the production quantum backend?",
            category="unanswerable",
            split="test",
            answerable=False,
            relevant=(),
        ),
    )

    report = evaluate_retrieval_v2(
        queries,
        {
            "q-answerable": (
                "context.md#Artifact",
                "other.md#Noise",
                "context.md#Budget",
            ),
            "q-no-answer": (),
        },
        top_k=3,
    )

    assert report.query_count == 2
    assert report.answerable_query_count == 1
    assert report.unanswerable_query_count == 1
    assert report.recall_at_k == 1.0
    assert report.precision_at_k == 2 / 3
    assert report.mrr == 1.0
    assert 0.8 < report.ndcg_at_k < 1.0
    assert report.unanswerable_accuracy == 1.0
    assert report.categories["real_user"].query_count == 1
    assert report.splits["test"].query_count == 2


def test_v2_query_contract_rejects_inconsistent_answerability() -> None:
    with pytest.raises(ValueError, match="answerable query requires relevance judgments"):
        KnowledgeBenchmarkQueryV2(
            query_id="broken",
            query="broken",
            category="real_user",
            split="dev",
            answerable=True,
            relevant=(),
        )

    with pytest.raises(ValueError, match="unanswerable query cannot have relevance judgments"):
        KnowledgeBenchmarkQueryV2(
            query_id="broken-2",
            query="broken",
            category="unanswerable",
            split="test",
            answerable=False,
            relevant=(KnowledgeRelevanceJudgment("source#section", 1),),
        )


def test_committed_v2_dataset_has_frozen_distribution() -> None:
    path = Path(__file__).parents[3] / "evals" / "knowledge_benchmark_v2.jsonl"
    queries = load_benchmark_v2(path)

    assert len(queries) == 200
    assert len({item.query_id for item in queries}) == 200
    assert {item.category for item in queries} == {
        "legacy_migrated",
        "real_user",
        "paraphrase",
        "hard_negative",
        "multi_document",
        "unanswerable",
    }
    assert {
        category: sum(item.category == category for item in queries)
        for category in {item.category for item in queries}
    } == {
        "legacy_migrated": 50,
        "real_user": 60,
        "paraphrase": 30,
        "hard_negative": 20,
        "multi_document": 20,
        "unanswerable": 20,
    }
    assert sum(not item.answerable for item in queries) == 20


def test_committed_v2_manifest_matches_dataset_and_corpus() -> None:
    repo_root = Path(__file__).parents[3]

    manifest = load_manifest(
        repo_root,
        repo_root / "evals" / "knowledge_benchmark_v2_manifest.json",
    )

    assert manifest.benchmark_id == "sage-knowledge-v2"
    assert manifest.benchmark_revision == "2026-07-27.1"
    assert len(manifest.files) == 16


def test_committed_book_manifest_and_dataset_contract_is_ci_portable() -> None:
    repo_root = Path(__file__).parents[3]

    manifest = read_manifest(repo_root / "evals" / "book_learning_benchmark_v1_manifest.json")
    queries = load_benchmark_v2(repo_root / manifest.dataset)

    assert manifest.benchmark_id == "sage-book-learning-v1"
    assert hashlib.sha256((repo_root / manifest.dataset).read_bytes()).hexdigest() == (
        manifest.dataset_sha256
    )
    assert {Path(item.path).suffix for item in manifest.files} == {".txt"}
    assert len(queries) == 14
    assert sum(not item.answerable for item in queries) == 4
    assert sum(item.category == "multi_document" for item in queries) == 3


def test_benchmark_runs_and_reports_the_requested_chunk_strategy(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "book.txt"
    source.write_text(
        "CHAPTER I.\n\n" + "division of labour improves productive power. " * 20,
        encoding="utf-8",
    )
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        '{"id":"q1","query":"Why does division of labour help?",'
        '"category":"real_user","split":"test","answerable":true,'
        '"provenance":"test","relevant_passages":['
        '{"source":"book.txt","section":"CHAPTER I.","relevance":3}],'
        '"required_claims":[],"forbidden_claims":[]}\n',
        encoding="utf-8",
    )
    manifest = BenchmarkManifest(
        benchmark_id="book-test",
        benchmark_revision="1",
        dataset="dataset.jsonl",
        dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
        corpus_root="corpus",
        files=(
            BenchmarkCorpusFile(
                path="book.txt",
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            ),
        ),
    )
    policy = KnowledgeAblationPolicy(
        strategy="parent_child",
        parent_child_max_chars=80,
        parent_child_overlap_chars=8,
    )

    report = run_benchmark(tmp_path, manifest, ablation_policy=policy)

    assert report["ablation_policy"]["strategy"] == "parent_child"
    assert report["index"]["active_chunk_count"] > 1
    assert report["chunking"] == {
        "eligible_block_count": 1,
        "indexed_block_count": 1,
        "block_coverage": 1.0,
        "planned_chunk_count": report["index"]["active_chunk_count"],
        "capacity_reached_source_count": 0,
        "index_truncated": False,
    }
    assert report["cases"][0]["latency_ms"] >= 0


def test_benchmark_prepares_role_aware_documents_and_queries_separately(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    source = corpus / "book.txt"
    source.write_text("CHAPTER I.\n\nEvidence for the query.", encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        '{"id":"q1","query":"evidence query", "category":"real_user",'
        '"split":"test","answerable":true,"provenance":"test",'
        '"relevant_passages":[{"source":"book.txt","section":"CHAPTER I.",'
        '"relevance":3}],"required_claims":[],"forbidden_claims":[]}\n',
        encoding="utf-8",
    )
    manifest = BenchmarkManifest(
        benchmark_id="book-role-test",
        benchmark_revision="1",
        dataset="dataset.jsonl",
        dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
        corpus_root="corpus",
        files=(
            BenchmarkCorpusFile(
                path="book.txt",
                sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            ),
        ),
    )
    provider = _RoleAwareBenchmarkProvider()

    run_benchmark(tmp_path, manifest, provider=provider)

    assert provider.documents
    assert provider.queries == ("evidence query",)


def test_benchmark_uses_custom_index_factory_and_cleans_it_on_success(tmp_path: Path) -> None:
    manifest = _small_benchmark_fixture(tmp_path)
    indexes: list[_TrackingBenchmarkIndex] = []

    def factory(
        workspace_id: str,
        provider: DenseEmbeddingProvider,
        policy: KnowledgeAblationPolicy,
    ) -> _TrackingBenchmarkIndex:
        index = _TrackingBenchmarkIndex(
            workspace_id=workspace_id,
            embedding_provider=provider,
            ablation_policy=policy,
        )
        indexes.append(index)
        return index

    report = run_benchmark(tmp_path, manifest, index_factory=factory)

    assert report["index"]["backend"] == "test-tracking-index"
    assert len(indexes) == 1
    assert indexes[0].search_calls == 1
    assert indexes[0].workspace_deleted
    assert indexes[0].closed


def test_benchmark_cleans_custom_index_when_query_fails(tmp_path: Path) -> None:
    manifest = _small_benchmark_fixture(tmp_path)
    indexes: list[_TrackingBenchmarkIndex] = []

    def factory(
        workspace_id: str,
        provider: DenseEmbeddingProvider,
        policy: KnowledgeAblationPolicy,
    ) -> _TrackingBenchmarkIndex:
        index = _TrackingBenchmarkIndex(
            workspace_id=workspace_id,
            embedding_provider=provider,
            ablation_policy=policy,
            fail_search=True,
        )
        indexes.append(index)
        return index

    with pytest.raises(RuntimeError, match="simulated benchmark query failure"):
        run_benchmark(tmp_path, manifest, index_factory=factory)

    assert len(indexes) == 1
    assert indexes[0].search_calls == 1
    assert indexes[0].workspace_deleted
    assert indexes[0].closed


def test_benchmark_cleans_custom_index_when_provider_preparation_fails(
    tmp_path: Path,
) -> None:
    manifest = _small_benchmark_fixture(tmp_path)
    indexes: list[_TrackingBenchmarkIndex] = []

    def factory(
        workspace_id: str,
        provider: DenseEmbeddingProvider,
        policy: KnowledgeAblationPolicy,
    ) -> _TrackingBenchmarkIndex:
        index = _TrackingBenchmarkIndex(
            workspace_id=workspace_id,
            embedding_provider=provider,
            ablation_policy=policy,
        )
        indexes.append(index)
        return index

    with pytest.raises(RuntimeError, match="simulated provider preparation failure"):
        run_benchmark(
            tmp_path,
            manifest,
            provider=_FailingPrepareProvider(),
            index_factory=factory,
        )

    assert len(indexes) == 1
    assert indexes[0].workspace_deleted
    assert indexes[0].closed


def test_benchmark_preserves_query_error_when_cleanup_also_fails(tmp_path: Path) -> None:
    manifest = _small_benchmark_fixture(tmp_path)
    indexes: list[_TrackingBenchmarkIndex] = []

    def factory(
        workspace_id: str,
        provider: DenseEmbeddingProvider,
        policy: KnowledgeAblationPolicy,
    ) -> _TrackingBenchmarkIndex:
        index = _TrackingBenchmarkIndex(
            workspace_id=workspace_id,
            embedding_provider=provider,
            ablation_policy=policy,
            fail_search=True,
            fail_cleanup=True,
        )
        indexes.append(index)
        return index

    with pytest.raises(RuntimeError, match="simulated benchmark query failure"):
        run_benchmark(tmp_path, manifest, index_factory=factory)

    assert indexes[0].workspace_deleted
    assert indexes[0].closed


def test_benchmark_rejects_invalid_retrieval_mode_before_building(tmp_path: Path) -> None:
    manifest = _small_benchmark_fixture(tmp_path)
    indexes: list[_TrackingBenchmarkIndex] = []

    def factory(
        workspace_id: str,
        provider: DenseEmbeddingProvider,
        policy: KnowledgeAblationPolicy,
    ) -> _TrackingBenchmarkIndex:
        index = _TrackingBenchmarkIndex(
            workspace_id=workspace_id,
            embedding_provider=provider,
            ablation_policy=policy,
        )
        indexes.append(index)
        return index

    with pytest.raises(ValueError, match="invalid benchmark retrieval mode"):
        run_benchmark(tmp_path, manifest, index_factory=factory, retrieval_mode="invalid")

    assert indexes == []


def test_manifest_uses_an_explicit_corpus_allowlist(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    selected = corpus / "selected.md"
    selected.write_text("# Selected\n", encoding="utf-8")
    (corpus / "report-not-in-benchmark.md").write_text("# Report\n", encoding="utf-8")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text(
        '{"id":"q1","query":"selected","category":"real_user","split":"test",'
        '"answerable":true,"provenance":"test","relevant_passages":['
        '{"source":"selected.md","section":"Selected","relevance":3}],'
        '"required_claims":[],"forbidden_claims":[]}\n',
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "benchmark_id": "test",
                "benchmark_revision": "1",
                "dataset": "dataset.jsonl",
                "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
                "corpus_root": "corpus",
                "files": [
                    {
                        "path": "selected.md",
                        "sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = load_manifest(tmp_path, manifest_path)

    assert [item.path for item in manifest.files] == ["selected.md"]
