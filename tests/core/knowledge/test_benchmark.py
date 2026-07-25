from __future__ import annotations

import hashlib
import json
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
from core.knowledge.benchmark_runner import load_manifest


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
    assert manifest.benchmark_revision == "2026-07-25.2"
    assert len(manifest.files) == 16


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
