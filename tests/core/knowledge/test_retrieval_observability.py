from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from core.knowledge.index import LocalKnowledgeIndex
from core.knowledge.observability import KnowledgeRetrievalObservabilityConfig
from core.knowledge.relevance import KnowledgeRelevancePolicy
from core.knowledge.retrieval import HashingEmbeddingProvider
from core.knowledge.store import KnowledgeSourceRoot, KnowledgeStore

_TEST_HMAC_KEY = "test-only-retrieval-observability-key-v1"


def _store(tmp_path: Path, *, observable: bool = True) -> tuple[KnowledgeStore, Path]:
    source = tmp_path / "source"
    source.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    database = tmp_path / "knowledge.sqlite3"
    index = LocalKnowledgeIndex(
        embedding_provider=HashingEmbeddingProvider(dimensions=64),
        observability=KnowledgeRetrievalObservabilityConfig(
            enabled=observable,
            hmac_key=_TEST_HMAC_KEY if observable else "",
        ),
    )
    store = KnowledgeStore(
        workspace,
        database,
        {
            "official": KnowledgeSourceRoot(
                root_id="official",
                kind="markdown",
                label="Official",
                path=source,
            )
        },
        knowledge_index=index,
    )
    store.initialize()
    return store, source


def _runs(database: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT * FROM knowledge_retrieval_runs ORDER BY created_at, run_id"
        ).fetchall()


def test_sqlite_trace_is_private_bounded_and_disabled_mode_preserves_results(
    tmp_path: Path,
) -> None:
    store, source = _store(tmp_path)
    secret_query = "TRACE_SECRET graph_state checkpoint"
    source_text = "# LangGraph\n\ngraph_state checkpoint recovery keeps durable progress.\n"
    (source / "langgraph.md").write_text(source_text, encoding="utf-8")
    proposal = store.ingest("official", "langgraph.md")
    store.approve(proposal.proposal_id, proposal.revision)

    observed = store.search(secret_query, retrieval_mode="hybrid", top_k=4)
    rows = _runs(store.database_path)

    assert observed
    assert len(rows) == 1
    run = rows[0]
    assert str(run["query_hash"]).startswith("hmac-sha256:")
    assert secret_query not in str(run["query_hash"])
    assert run["query_length"] == len(secret_query)
    assert run["round_index"] == 1
    assert run["rewrite_hash"] is None
    assert run["retrieval_mode"] == "hybrid"
    assert run["top_k"] == 4
    assert run["candidate_count"] >= run["returned_count"] == len(observed)
    assert run["gate_decision"] == "not_configured"
    assert run["failure_type"] == "none"
    assert run["error_type"] is None
    assert 0.0 <= run["result_coverage"] <= 1.0
    candidates = json.loads(str(run["candidates_json"]))
    assert candidates[0]["chunk_id"] == observed[0].chunk.chunk_id
    assert candidates[0]["rank"] == 1
    assert "rrf_score" in candidates[0]
    serialized = json.dumps(dict(run), ensure_ascii=False)
    assert "TRACE_SECRET" not in serialized
    assert source_text not in serialized

    store.knowledge_index = LocalKnowledgeIndex(
        embedding_provider=HashingEmbeddingProvider(dimensions=64),
        observability=KnowledgeRetrievalObservabilityConfig(),
    )
    unobserved = store.search(secret_query, retrieval_mode="hybrid", top_k=4)

    assert unobserved == observed
    assert len(_runs(store.database_path)) == 1


def test_sqlite_trace_classifies_ingestion_retrieval_and_gate_rejection(tmp_path: Path) -> None:
    store, source = _store(tmp_path)

    assert store.search("nothing indexed", retrieval_mode="sparse") == ()
    (source / "docker.md").write_text(
        "# Docker\n\nBuildKit cache mounts speed up repeat builds.\n",
        encoding="utf-8",
    )
    proposal = store.ingest("official", "docker.md")
    store.approve(proposal.proposal_id, proposal.revision)
    assert store.search("term-that-does-not-exist", retrieval_mode="sparse") == ()

    provider = HashingEmbeddingProvider(dimensions=64)
    corpus_revision = store.index_summary().corpus_revision
    store.knowledge_index = LocalKnowledgeIndex(
        embedding_provider=provider,
        relevance_policy=KnowledgeRelevancePolicy(
            benchmark_id="benchmark",
            benchmark_revision="v1",
            corpus_revision=corpus_revision,
            embedding_model=provider.model_id,
            embedding_revision=provider.model_revision,
            top_k=8,
            min_sparse_score=1_000_000.0,
            min_dense_score=1_000_000.0,
        ),
        observability=KnowledgeRetrievalObservabilityConfig(
            enabled=True,
            hmac_key=_TEST_HMAC_KEY,
        ),
    )
    assert store.search("BuildKit cache", retrieval_mode="hybrid") == ()

    rows = _runs(store.database_path)
    assert [row["failure_type"] for row in rows] == [
        "ingestion",
        "retrieval",
        "gate_rejected",
    ]
    assert rows[-1]["candidate_count"] > 0
    assert rows[-1]["returned_count"] == 0
    assert rows[-1]["gate_decision"] == "rejected"


def test_observability_requires_a_nontrivial_hmac_key() -> None:
    try:
        KnowledgeRetrievalObservabilityConfig(enabled=True, hmac_key="short")
    except ValueError as exc:
        assert "HMAC key" in str(exc)
    else:
        raise AssertionError("enabled observability accepted a weak HMAC key")


def test_sqlite_trace_records_only_exception_type_for_provider_failure(tmp_path: Path) -> None:
    class FailingQueryProvider(HashingEmbeddingProvider):
        def embed(self, text: str) -> tuple[float, ...]:
            if "FAIL_QUERY_SECRET" in text:
                raise RuntimeError("provider detail must not be persisted")
            return super().embed(text)

    store, source = _store(tmp_path)
    (source / "k8s.md").write_text(
        "# Kubernetes\n\nReadiness probes remove unhealthy pods from service endpoints.\n",
        encoding="utf-8",
    )
    proposal = store.ingest("official", "k8s.md")
    store.approve(proposal.proposal_id, proposal.revision)
    store.knowledge_index = LocalKnowledgeIndex(
        embedding_provider=FailingQueryProvider(dimensions=64),
        observability=KnowledgeRetrievalObservabilityConfig(
            enabled=True,
            hmac_key=_TEST_HMAC_KEY,
        ),
    )

    try:
        store.search("FAIL_QUERY_SECRET", retrieval_mode="dense")
    except RuntimeError as exc:
        assert "provider detail" in str(exc)
    else:
        raise AssertionError("failing provider did not raise")

    run = _runs(store.database_path)[0]
    assert run["failure_type"] == "system"
    assert run["error_type"] == "RuntimeError"
    serialized = json.dumps(dict(run), ensure_ascii=False)
    assert "FAIL_QUERY_SECRET" not in serialized
    assert "provider detail" not in serialized


def test_trace_persistence_failure_is_sanitized_and_does_not_break_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    store, source = _store(tmp_path)
    (source / "spring.md").write_text(
        "# Spring\n\nTransaction boundaries belong at service methods.\n",
        encoding="utf-8",
    )
    proposal = store.ingest("official", "spring.md")
    store.approve(proposal.proposal_id, proposal.revision)
    index = store.knowledge_index

    def fail_persistence(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("TRACE_STORAGE_SECRET")

    monkeypatch.setattr(index, "_insert_retrieval_trace", fail_persistence)
    hits = store.search("Transaction boundaries", retrieval_mode="hybrid")

    assert hits
    assert _runs(store.database_path) == []
    assert "RuntimeError" in caplog.text
    assert "TRACE_STORAGE_SECRET" not in caplog.text
