from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from core.knowledge.benchmark import (
    KnowledgeBenchmarkQueryV2,
    KnowledgeRelevanceJudgment,
)
from core.knowledge.index import LocalKnowledgeIndex
from core.knowledge.relevance import (
    KnowledgeRelevancePolicy,
    KnowledgeRelevancePolicyError,
    load_relevance_policy,
)
from core.knowledge.relevance_calibration import calibrate_relevance_policy, calibration_result_dict
from core.knowledge.retrieval import HashingEmbeddingProvider
from core.knowledge.store import KnowledgeSourceRoot, KnowledgeStore


def _policy(**overrides: object) -> KnowledgeRelevancePolicy:
    values: dict[str, object] = {
        "benchmark_id": "sage-knowledge-v2",
        "benchmark_revision": "sha256:benchmark",
        "corpus_revision": "kcorpus_test",
        "embedding_model": "sage.hashing",
        "embedding_revision": "1.0.0",
        "top_k": 10,
        "min_sparse_score": 4.0,
        "min_dense_score": None,
    }
    values.update(overrides)
    return KnowledgeRelevancePolicy(**values)  # type: ignore[arg-type]


def test_relevance_policy_round_trip_and_provider_binding(tmp_path: Path) -> None:
    policy = _policy()
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy.to_dict()), encoding="utf-8")

    loaded = load_relevance_policy(path)

    assert loaded == policy
    assert loaded.policy_id.startswith("krp_")
    assert loaded.accepts(sparse_score=4.0, dense_score=None)
    assert not loaded.accepts(sparse_score=3.99, dense_score=0.99)
    with pytest.raises(KnowledgeRelevancePolicyError, match="does not match embedding provider"):
        LocalKnowledgeIndex(
            embedding_provider=HashingEmbeddingProvider(),
            relevance_policy=_policy(embedding_revision="old"),
        )


def test_v2_relevance_policy_uses_route_specific_hybrid_threshold(tmp_path: Path) -> None:
    policy = _policy(
        min_dense_score=0.75,
        min_hybrid_score=0.03,
        schema_version=2,
    )
    path = tmp_path / "policy-v2.json"
    path.write_text(json.dumps(policy.to_dict()), encoding="utf-8")

    loaded = load_relevance_policy(path)

    assert loaded == policy
    assert loaded.accepts(
        sparse_score=10.0,
        dense_score=0.90,
        hybrid_score=0.031,
        retrieval_mode="hybrid",
    )
    assert not loaded.accepts(
        sparse_score=10.0,
        dense_score=0.90,
        hybrid_score=0.029,
        retrieval_mode="hybrid",
    )
    assert loaded.accepts(
        sparse_score=4.0,
        dense_score=None,
        hybrid_score=None,
        retrieval_mode="sparse",
    )


def test_relevance_policy_rejects_search_above_calibrated_top_k(tmp_path: Path) -> None:
    index = LocalKnowledgeIndex(relevance_policy=_policy(top_k=4))
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    index.ensure_schema(connection)

    with pytest.raises(ValueError, match="exceeds calibrated"):
        index.search(connection, "evidence", top_k=5)


def test_local_index_migrates_existing_chunks_to_multimodal_contract() -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(
        """
        CREATE TABLE knowledge_chunks (
            chunk_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, page_id TEXT NOT NULL,
            page_revision TEXT NOT NULL, page_path TEXT NOT NULL, source_id TEXT NOT NULL,
            source_revision TEXT NOT NULL, source_kind TEXT NOT NULL,
            source_relative_path TEXT NOT NULL, proposal_id TEXT NOT NULL, artifact_id TEXT,
            block_id TEXT NOT NULL, ordinal INTEGER NOT NULL, title TEXT NOT NULL,
            heading_path_json TEXT NOT NULL, page_number INTEGER, text TEXT NOT NULL,
            token_count INTEGER NOT NULL, content_hash TEXT NOT NULL, visibility TEXT NOT NULL,
            language TEXT NOT NULL, active INTEGER NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(page_revision, ordinal)
        )
        """
    )

    LocalKnowledgeIndex().ensure_schema(connection)

    columns = {
        str(row["name"]): str(row["dflt_value"])
        for row in connection.execute("PRAGMA table_info(knowledge_chunks)")
    }
    assert {
        "block_kind",
        "bbox_json",
        "media_ref",
        "confidence",
        "parser_id",
        "parser_version",
        "line_start",
        "line_end",
        "char_start",
        "char_end",
        "byte_start",
        "byte_end",
        "parent_chunk_id",
        "retrieval_description",
        "retrieval_description_provider",
        "retrieval_description_revision",
    } <= (columns.keys())
    assert columns["block_kind"] == "'paragraph'"
    assert columns["confidence"] == "1.0"


def test_relevance_policy_rejects_tampered_policy_id() -> None:
    raw = _policy().to_dict()
    raw["policy_id"] = "krp_tampered"

    with pytest.raises(ValueError, match="id does not match"):
        KnowledgeRelevancePolicy.from_dict(raw)


def test_index_policy_can_return_explicit_no_evidence(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    repository = tmp_path / "knowledge"
    vault.mkdir()
    repository.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )
    (vault / "harness.md").write_text(
        "# Harness\n\n受控工具调用需要策略校验和审批。\n", encoding="utf-8"
    )
    store = KnowledgeStore(
        repository,
        tmp_path / "knowledge.sqlite3",
        {"sage": KnowledgeSourceRoot(root_id="sage", kind="markdown", label="Sage", path=vault)},
    )
    store.initialize()
    store.evaluate_and_apply_policy(store.ingest("sage", "harness.md").proposal_id)
    index = LocalKnowledgeIndex(
        relevance_policy=_policy(
            min_sparse_score=1_000_000.0,
            corpus_revision=store.index_summary().corpus_revision,
        )
    )
    store.knowledge_index = index

    assert store.search("策略校验") == ()
    assert store.retrieve("策略校验").status == "no_evidence"
    assert store.index_summary().abstention_enabled is True
    assert store.index_summary().relevance_policy_id == index.relevance_policy.policy_id
    with sqlite3.connect(tmp_path / "knowledge.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0] == 1

    (vault / "harness.md").write_text(
        "# Harness\n\n策略校验更新后语料 revision 已变化。\n", encoding="utf-8"
    )
    store.evaluate_and_apply_policy(store.ingest("sage", "harness.md").proposal_id)
    assert store.index_summary().abstention_enabled is False
    with pytest.raises(KnowledgeRelevancePolicyError, match="does not match indexed corpus"):
        store.search("策略校验")


def test_calibration_uses_dev_and_reports_test_without_tuning() -> None:
    queries = (
        _query("dev-answer", "dev", True, "doc-answer"),
        _query("dev-none", "dev", False),
        _query("test-answer", "test", True, "doc-test"),
        _query("test-none", "test", False),
    )
    hits = {
        "dev-answer": (_hit("doc-answer", 10.0),),
        "dev-none": (_hit("doc-noise", 3.0),),
        "test-answer": (_hit("doc-test", 9.0),),
        "test-none": (_hit("doc-test-noise", 11.0),),
    }

    result = calibrate_relevance_policy(
        queries,
        hits,
        benchmark_id="sage-knowledge-v2",
        benchmark_revision="sha256:benchmark",
        corpus_revision="kcorpus_test",
        embedding_model="sage.hashing",
        embedding_revision="1.0.0",
        supports_semantic_recall=False,
        top_k=10,
    )

    assert result.dev_calibrated["recall_at_k"] == 1.0
    assert result.dev_calibrated["unanswerable_accuracy"] == 1.0
    assert result.test_calibrated["recall_at_k"] == 1.0
    assert result.test_calibrated["unanswerable_accuracy"] == 0.0
    assert 3.0 < (result.policy.min_sparse_score or 0.0) <= 10.0

    payload = calibration_result_dict(
        result,
        source_report={
            "benchmark_id": "sage-knowledge-v2",
            "benchmark_revision": "sha256:benchmark",
            "dataset_sha256": "sha256:dataset",
            "source_commit": "a" * 40,
            "source_dirty": False,
            "top_k": 10,
            "provider": {"model_id": "sage.hashing"},
            "index": {"corpus_revision": "kcorpus_test"},
        },
    )
    assert payload["source_commit"] == "a" * 40
    assert payload["source_dirty"] is False
    assert payload["index"] == {"corpus_revision": "kcorpus_test"}
    assert payload["policy"] == result.policy.to_dict()


def _query(
    query_id: str, split: str, answerable: bool, document_id: str = ""
) -> KnowledgeBenchmarkQueryV2:
    relevant = (
        (KnowledgeRelevanceJudgment(document_id=document_id, relevance=3),) if answerable else ()
    )
    return KnowledgeBenchmarkQueryV2(
        query_id=query_id,
        query=query_id,
        category="real_user" if answerable else "unanswerable",
        split=split,
        answerable=answerable,
        relevant=relevant,
    )


def _hit(passage_id: str, sparse_score: float) -> dict[str, object]:
    return {
        "passage_id": passage_id,
        "sparse_score": sparse_score,
        "dense_score": None,
    }
