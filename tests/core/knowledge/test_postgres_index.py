from __future__ import annotations

import json
import os
import subprocess
import uuid
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path

import psycopg2
import pytest
from PIL import Image, PngImagePlugin

from core.knowledge.observability import KnowledgeRetrievalObservabilityConfig
from core.knowledge.postgres_index import (
    POSTGRES_DESCRIBED_PARENT_CHILD_SCHEMA_REVISION,
    POSTGRES_INDEX_SCHEMA_REVISION,
    POSTGRES_MULTIMODAL_SCHEMA_REVISION,
    POSTGRES_RETRIEVAL_TRACE_SCHEMA_REVISION,
    POSTGRES_TEXT_LOCATOR_SCHEMA_REVISION,
    PostgresKnowledgeIndex,
    PostgresKnowledgeIndexConfig,
)
from core.knowledge.postgres_retrieval import PgTextsearchBm25Retriever
from core.knowledge.recovery import KnowledgeRecoveryPolicy
from core.knowledge.retrieval import HashingEmbeddingProvider
from core.knowledge.store import KnowledgeSourceRoot, KnowledgeStore

pytestmark = pytest.mark.postgres


@pytest.fixture
def postgres_dsn() -> str:
    value = os.environ.get("SAGE_TEST_POSTGRES_DSN", "").strip()
    if not value:
        pytest.skip("SAGE_TEST_POSTGRES_DSN is required for PostgreSQL integration tests")
    return value


@pytest.fixture
def postgres_bm25_dsn() -> str:
    value = os.environ.get("SAGE_TEST_POSTGRES_BM25_DSN", "").strip()
    if not value:
        pytest.skip("SAGE_TEST_POSTGRES_BM25_DSN is required for pg_textsearch integration tests")
    return value


@pytest.fixture
def postgres_store(tmp_path: Path, postgres_dsn: str) -> Iterator[KnowledgeStore]:
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
    workspace_id = f"pg-test-{uuid.uuid4().hex}"
    index = PostgresKnowledgeIndex(
        PostgresKnowledgeIndexConfig(dsn=postgres_dsn),
        workspace_id=workspace_id,
        embedding_provider=HashingEmbeddingProvider(dimensions=64),
    )
    store = KnowledgeStore(
        workspace,
        tmp_path / "canonical.sqlite3",
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
    try:
        yield store
    finally:
        index.delete_workspace()
        index.close()


def test_postgres_schema_has_gin_and_no_ann_indexes(postgres_store: KnowledgeStore) -> None:
    postgres_store.initialize()
    index = postgres_store.knowledge_index
    assert isinstance(index, PostgresKnowledgeIndex)

    with psycopg2.connect(index.config.dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename LIKE 'knowledge_index_%'
            ORDER BY indexname
            """
        )
        indexes = tuple((str(row[0]), str(row[1])) for row in cursor.fetchall())
        cursor.execute("SELECT revision FROM knowledge_index_schema_migrations ORDER BY revision")
        revisions = {str(row[0]) for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema()
              AND table_name='knowledge_index_source_revisions'
            """
        )
        revision_columns = {str(row[0]) for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema()
              AND table_name='knowledge_index_chunks'
            """
        )
        chunk_columns = {str(row[0]) for row in cursor.fetchall()}
        cursor.execute(
            """
            SELECT COUNT(*) FROM pg_stat_activity
            WHERE application_name='sage-knowledge-index'
              AND state='idle in transaction'
            """
        )
        idle_in_transaction = int(cursor.fetchone()[0])

    assert any("USING gin (search_tsv)" in definition for _name, definition in indexes)
    assert all("hnsw" not in definition.casefold() for _name, definition in indexes)
    assert all("ivfflat" not in definition.casefold() for _name, definition in indexes)
    assert POSTGRES_INDEX_SCHEMA_REVISION in revisions
    assert POSTGRES_RETRIEVAL_TRACE_SCHEMA_REVISION in revisions
    assert POSTGRES_MULTIMODAL_SCHEMA_REVISION in revisions
    assert POSTGRES_DESCRIBED_PARENT_CHILD_SCHEMA_REVISION in revisions
    assert POSTGRES_TEXT_LOCATOR_SCHEMA_REVISION in revisions
    assert "embedding_dimensions" in revision_columns
    assert {
        "block_kind",
        "bbox",
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
    } <= chunk_columns
    assert idle_in_transaction == 0


def test_pg_textsearch_bm25_retrieval_is_a_replaceable_sparse_route(
    tmp_path: Path,
    postgres_bm25_dsn: str,
) -> None:
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
    workspace_id = f"pg-bm25-test-{uuid.uuid4().hex}"
    index = PostgresKnowledgeIndex(
        PostgresKnowledgeIndexConfig(dsn=postgres_bm25_dsn),
        workspace_id=workspace_id,
        embedding_provider=HashingEmbeddingProvider(dimensions=64),
        sparse_retriever=PgTextsearchBm25Retriever(
            index_name=f"knowledge_bm25_{uuid.uuid4().hex}_idx"
        ),
    )
    store = KnowledgeStore(
        workspace,
        tmp_path / "canonical.sqlite3",
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
    try:
        (source / "book.md").write_text(
            "# Division\n\nDivision of labour raises productive power.\n",
            encoding="utf-8",
        )
        proposal = store.ingest("official", "book.md")
        store.approve(proposal.proposal_id, proposal.revision)

        hits = store.search("division labour productive", retrieval_mode="sparse", top_k=3)

        assert hits
        assert index.backend_id.startswith("pg-textsearch-bm25+")
        with psycopg2.connect(postgres_bm25_dsn) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT extversion FROM pg_extension WHERE extname='pg_textsearch'")
            assert cursor.fetchone() is not None
    finally:
        index.delete_workspace()
        index.close()


def test_postgres_retrieval_trace_excludes_query_and_chunk_text(
    postgres_store: KnowledgeStore,
    tmp_path: Path,
) -> None:
    index = postgres_store.knowledge_index
    assert isinstance(index, PostgresKnowledgeIndex)
    index.observability = KnowledgeRetrievalObservabilityConfig(
        enabled=True,
        hmac_key="test-only-postgres-observability-key-v1",
    )
    source_text = "# Spring\n\nTransactional boundaries belong at service methods.\n"
    (tmp_path / "source" / "spring.md").write_text(source_text, encoding="utf-8")
    proposal = postgres_store.ingest("official", "spring.md")
    postgres_store.approve(proposal.proposal_id, proposal.revision)

    query = "POSTGRES_TRACE_SECRET Transactional boundaries"
    hits = postgres_store.search(query, retrieval_mode="hybrid", top_k=4)

    assert hits
    with psycopg2.connect(index.config.dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT query_hash, query_length, round_index, rewrite_hash,
                   candidate_count, returned_count, candidates_json,
                   gate_decision, failure_type, error_type
            FROM knowledge_retrieval_runs
            WHERE workspace_id=%s
            ORDER BY created_at DESC LIMIT 1
            """,
            (index.workspace_id,),
        )
        row = cursor.fetchone()

    assert row is not None
    serialized = json.dumps(row, ensure_ascii=False, default=str)
    assert str(row[0]).startswith("hmac-sha256:")
    assert row[1:4] == (len(query), 1, None)
    assert row[4] >= row[5] == len(hits)
    assert row[7:] == ("not_configured", "none", None)
    assert "POSTGRES_TRACE_SECRET" not in serialized
    assert source_text not in serialized


def test_postgres_recovery_trace_links_both_rounds_without_raw_queries(
    postgres_store: KnowledgeStore,
    tmp_path: Path,
) -> None:
    index = postgres_store.knowledge_index
    assert isinstance(index, PostgresKnowledgeIndex)
    index.observability = KnowledgeRetrievalObservabilityConfig(
        enabled=True,
        hmac_key="test-only-postgres-observability-key-v1",
    )
    postgres_store.recovery_policy = KnowledgeRecoveryPolicy(enabled=True, min_results=4)
    source_text = (
        "# Filtering\n\nWith approximate indexes, filtering can produce fewer matching rows. "
        "Iterative scans continue until enough rows are found.\n"
    )
    (tmp_path / "source" / "pgvector.md").write_text(source_text, encoding="utf-8")
    proposal = postgres_store.ingest("official", "pgvector.md")
    postgres_store.approve(proposal.proposal_id, proposal.revision)
    query = "为什么 HNSW 加过滤条件后可能返回不足 top-k？"

    bundle = postgres_store.retrieve(query, top_k=8)

    assert bundle.recovery_status == "recovered"
    with psycopg2.connect(index.config.dsn) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT query_hash, round_index, rewrite_hash
            FROM knowledge_retrieval_runs
            WHERE workspace_id=%s
            ORDER BY created_at, run_id
            """,
            (index.workspace_id,),
        )
        rows = cursor.fetchall()

    assert [row[1] for row in rows] == [1, 2]
    assert rows[0][0] == rows[1][0]
    assert rows[0][2] is None
    assert str(rows[1][2]).startswith("hmac-sha256:")
    serialized = json.dumps(rows, ensure_ascii=False, default=str)
    assert query not in serialized
    assert "iterative scans" not in serialized


def test_postgres_exact_routes_preserve_filters_revisions_and_citations(
    postgres_store: KnowledgeStore,
    tmp_path: Path,
) -> None:
    source = tmp_path / "source" / "retrieval.md"
    source.write_text(
        "# Agent Retrieval\n\n旧版使用 context_schema 与 /api/v1/agent/run。\n",
        encoding="utf-8",
    )
    first = postgres_store.ingest("official", "retrieval.md")
    postgres_store.approve(first.proposal_id, first.revision)
    old_hit = postgres_store.search("context_schema", retrieval_mode="sparse")[0]

    source.write_text(
        "# Agent Retrieval\n\n新版使用 graph_state 与 /api/v2/agent/run。\n",
        encoding="utf-8",
    )
    second = postgres_store.ingest("official", "retrieval.md")
    postgres_store.approve(second.proposal_id, second.revision)

    sparse = postgres_store.search("graph_state /api/v2", retrieval_mode="sparse")
    dense = postgres_store.search("graph_state", retrieval_mode="dense")
    hybrid = postgres_store.search("graph_state", retrieval_mode="hybrid")
    stale_default = postgres_store.search("context_schema", retrieval_mode="sparse")
    historical = postgres_store.search(
        "context_schema",
        page_revisions=(old_hit.chunk.page_revision,),
        retrieval_mode="sparse",
    )

    assert sparse and sparse[0].sparse_rank == 1 and sparse[0].dense_rank is None
    assert dense and dense[0].dense_rank == 1 and dense[0].sparse_rank is None
    assert hybrid and hybrid[0].sparse_rank == 1 and hybrid[0].dense_rank == 1
    assert all(hit.chunk.source_id == second.source_id for hit in sparse)
    assert (
        postgres_store.search("graph_state", source_ids=("unknown",), retrieval_mode="sparse") == ()
    )
    assert postgres_store.search("graph_state", visibility="public", retrieval_mode="sparse") == ()
    assert stale_default == ()
    assert historical[0].chunk.page_revision == old_hit.chunk.page_revision
    assert historical[0].chunk.active is False
    assert postgres_store.citation(sparse[0].citation_id).chunk_id == sparse[0].chunk.chunk_id
    with pytest.raises(KeyError, match="stale or unknown"):
        postgres_store.citation(old_hit.citation_id)


def test_postgres_visual_chunk_round_trip_preserves_normalized_region(
    postgres_store: KnowledgeStore,
    tmp_path: Path,
) -> None:
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Description", "PostgreSQL visual exact scan P95 is 36 milliseconds")
    payload = BytesIO()
    Image.new("RGB", (320, 180), "white").save(payload, format="PNG", pnginfo=metadata)
    (tmp_path / "source" / "retrieval.png").write_bytes(payload.getvalue())
    proposal = postgres_store.ingest("official", "retrieval.png")
    postgres_store.approve(proposal.proposal_id, proposal.revision)

    hit = postgres_store.search(
        "PostgreSQL visual exact scan P95 36 milliseconds",
        retrieval_mode="sparse",
    )[0]
    citation = postgres_store.citation(hit.citation_id)

    assert citation.block_kind == "media"
    assert citation.page_number == 1
    assert citation.bbox == (0.0, 0.0, 1.0, 1.0)
    assert citation.media_ref == "retrieval.png"
    assert citation.confidence == 1.0
    assert citation.parser_id == "sage.png"
    assert citation.parser_version == "1.0.0"


def test_postgres_force_rebuild_is_idempotent_and_preserves_stable_ids(
    postgres_store: KnowledgeStore,
    tmp_path: Path,
) -> None:
    (tmp_path / "source" / "langgraph.md").write_text(
        "# LangGraph\n\nCommand supports goto and update fields.\n",
        encoding="utf-8",
    )
    proposal = postgres_store.ingest("official", "langgraph.md")
    postgres_store.approve(proposal.proposal_id, proposal.revision)
    before = postgres_store.search("Command goto", retrieval_mode="sparse")[0]

    first = postgres_store.rebuild_index()
    second = postgres_store.rebuild_index()
    after = postgres_store.search("Command goto", retrieval_mode="sparse")[0]

    assert first.error_count == second.error_count == 0
    assert first.indexed_revision_count == second.indexed_revision_count == 1
    assert first.active_chunk_count == second.active_chunk_count == 1
    assert after.chunk.chunk_id == before.chunk.chunk_id
    assert after.citation_id == before.citation_id
    assert after.chunk.page_revision == before.chunk.page_revision


def test_postgres_dimension_change_rebuilds_matching_provider_revision(
    postgres_store: KnowledgeStore,
    postgres_dsn: str,
    tmp_path: Path,
) -> None:
    (tmp_path / "source" / "dimensions.md").write_text(
        "# Dimensions\n\nEmbedding dimensions are part of the projection contract.\n",
        encoding="utf-8",
    )
    proposal = postgres_store.ingest("official", "dimensions.md")
    postgres_store.approve(proposal.proposal_id, proposal.revision)
    original = postgres_store.knowledge_index
    assert isinstance(original, PostgresKnowledgeIndex)
    resized = PostgresKnowledgeIndex(
        PostgresKnowledgeIndexConfig(dsn=postgres_dsn),
        workspace_id=original.workspace_id,
        embedding_provider=HashingEmbeddingProvider(dimensions=32),
    )
    resized_store = KnowledgeStore(
        postgres_store.workspace_root,
        postgres_store.database_path,
        postgres_store.source_roots,
        knowledge_index=resized,
    )
    try:
        summary = resized_store.index_summary()
        assert summary.error_count == 0
        assert resized_store.search("projection contract", retrieval_mode="dense")
        with psycopg2.connect(postgres_dsn) as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT embedding_dimensions FROM knowledge_index_chunks
                WHERE workspace_id=%s
                """,
                (original.workspace_id,),
            )
            assert cursor.fetchall() == [(32,)]
    finally:
        resized.close()


def test_postgres_embedding_provider_distinguishes_document_and_query_roles(
    postgres_store: KnowledgeStore,
    tmp_path: Path,
) -> None:
    class RoleAwareProvider(HashingEmbeddingProvider):
        supports_semantic_recall = True

        def __init__(self) -> None:
            super().__init__(dimensions=64)
            self.model_id = "test.postgres-role-aware"
            self.model_revision = "v1"
            self.document_inputs: list[str] = []
            self.query_inputs: list[str] = []

        def embed_document(self, text: str) -> tuple[float, ...]:
            self.document_inputs.append(text)
            return super().embed(text)

        def embed_query(self, text: str) -> tuple[float, ...]:
            self.query_inputs.append(text)
            return super().embed(text)

        def embed(self, text: str) -> tuple[float, ...]:
            raise AssertionError("role-aware callers must not use the compatibility embed method")

    provider = RoleAwareProvider()
    index = postgres_store.knowledge_index
    assert isinstance(index, PostgresKnowledgeIndex)
    index.embedding_provider = provider
    (tmp_path / "source" / "roles.md").write_text(
        "# Roles\n\nPostgreSQL 使用 GIN 与 pgvector 形成双路召回。\n",
        encoding="utf-8",
    )

    proposal = postgres_store.ingest("official", "roles.md")
    postgres_store.approve(proposal.proposal_id, proposal.revision)
    hits = postgres_store.search("PostgreSQL 双路召回", retrieval_mode="dense")

    assert hits
    assert provider.document_inputs
    assert provider.query_inputs == ["PostgreSQL 双路召回"]


def test_postgres_failed_revision_is_atomic_and_rebuildable(
    tmp_path: Path,
    postgres_dsn: str,
) -> None:
    class FailingProvider(HashingEmbeddingProvider):
        def embed(self, text: str) -> tuple[float, ...]:
            if "FAIL_INDEX" in text:
                raise RuntimeError("synthetic projection failure")
            return super().embed(text)

    source = tmp_path / "source"
    source.mkdir()
    (source / "failure.md").write_text(
        "# Failure\n\n第一段可索引。\n\nFAIL_INDEX 触发失败。\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    workspace_id = f"pg-failure-{uuid.uuid4().hex}"
    failing = PostgresKnowledgeIndex(
        PostgresKnowledgeIndexConfig(dsn=postgres_dsn),
        workspace_id=workspace_id,
        embedding_provider=FailingProvider(dimensions=64),
    )
    store = KnowledgeStore(
        workspace,
        tmp_path / "canonical.sqlite3",
        {
            "official": KnowledgeSourceRoot(
                root_id="official",
                kind="markdown",
                label="Official",
                path=source,
            )
        },
        knowledge_index=failing,
    )
    try:
        proposal = store.ingest("official", "failure.md")
        store.approve(proposal.proposal_id, proposal.revision)
        failed = store.index_summary()

        assert failed.error_count == 1
        assert failed.total_chunk_count == 0

        recovered = PostgresKnowledgeIndex(
            PostgresKnowledgeIndexConfig(dsn=postgres_dsn),
            workspace_id=workspace_id,
            embedding_provider=HashingEmbeddingProvider(dimensions=64),
        )
        store.knowledge_index = recovered
        rebuilt = store.rebuild_index()

        assert rebuilt.error_count == 0
        assert rebuilt.active_chunk_count == 2
        assert store.search("触发失败", retrieval_mode="sparse")
        recovered.close()
    finally:
        failing.delete_workspace()
        failing.close()
