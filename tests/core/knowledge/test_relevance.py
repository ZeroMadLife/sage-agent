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
from core.knowledge.relevance_calibration import calibrate_relevance_policy
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


def test_relevance_policy_rejects_search_above_calibrated_top_k(tmp_path: Path) -> None:
    index = LocalKnowledgeIndex(relevance_policy=_policy(top_k=4))
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    index.ensure_schema(connection)

    with pytest.raises(ValueError, match="exceeds calibrated"):
        index.search(connection, "evidence", top_k=5)


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
